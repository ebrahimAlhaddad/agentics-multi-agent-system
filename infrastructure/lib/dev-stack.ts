import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecs_patterns from 'aws-cdk-lib/aws-ecs-patterns';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { RemovalPolicy, Stack, StackProps, CfnOutput } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';
import * as certificatemanager from 'aws-cdk-lib/aws-certificatemanager';
import * as cdk from 'aws-cdk-lib';
import * as elasticache from 'aws-cdk-lib/aws-elasticache';
import * as appscaling from 'aws-cdk-lib/aws-applicationautoscaling';

export class AgenticsDevStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);
    

    // #########################################################
    // VPC, ECS CLUSTER
    // #########################################################
    // 1. VPC
    const vpc = new ec2.Vpc(this, 'Vpc', { maxAzs: 2 });
    // 2. ECS Cluster
    const cluster = new ecs.Cluster(this, 'EcsCluster', { vpc });

    // #########################################################
    // REDIS
    // #########################################################  
    

    // 2. Cache Subnet Group for Redis nodes
    const subnetGroup = new elasticache.CfnSubnetGroup(this, 'RedisSubnetGroupDev', {
      description: 'Subnet group for Redis',
      subnetIds: vpc.selectSubnets({ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }).subnetIds,
      cacheSubnetGroupName: 'agentics-redis-subnet-group',
    });
    // 1. Security Group
    const redisSG = new ec2.SecurityGroup(this, 'RedisSGDev', {
      vpc,
      description: 'Allow ECS to access Redis Replication Group',
    });

    // 3. Redis Replication Group (primary + one replica) with failover
    const redisRG = new elasticache.CfnReplicationGroup(this, 'RedisReplicationGroupDev', {
      replicationGroupDescription: 'Highly available Redis for FastAPI ECS',
      engine: 'redis',
      cacheNodeType: 'cache.t3.micro',
      numNodeGroups: 1,                    // cluster-mode disabled
      replicasPerNodeGroup: 1,            // includes 1 replica
      automaticFailoverEnabled: true,
      // clusterMode: 'enabled',
      cacheSubnetGroupName: subnetGroup.cacheSubnetGroupName!,
      securityGroupIds: [redisSG.securityGroupId],
    });
    // Ensure Redis Replication Group waits for subnet group
    redisRG.node.addDependency(subnetGroup);

    // // 4. auto scale redis replica
    // const redisReplicaTarget = new appscaling.CfnScalableTarget(this, 'RedisReplicaScalableTargetDev', {
    //   serviceNamespace: 'elasticache',
    //   resourceId: `replication-group/${redisRG.ref}`,
    //   scalableDimension: 'elasticache:replication-group:Replicas',
    //   minCapacity: 1,
    //   maxCapacity: 5,
    // });

    // #########################################################
    // RDS
    // #########################################################
    // 1. RDS Instance
    const dbInstance = new rds.DatabaseInstance(this, 'PostgresDB', {
      engine: rds.DatabaseInstanceEngine.postgres({ version: rds.PostgresEngineVersion.VER_13 }),
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MICRO),
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      credentials: rds.Credentials.fromGeneratedSecret('Postgres'),
      databaseName: 'postgres',
      port: 8002,
      publiclyAccessible: false,
      maxAllocatedStorage: 150,      // max storage to autoscale to (optional)
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // #########################################################
    // FARGATE SERVICE
    // #########################################################
    // 1. Fargate Service
    const fastApiService = new ecs_patterns.ApplicationLoadBalancedFargateService(this, 'FastApiDevService', {
      cluster,
      cpu: 256,
      memoryLimitMiB: 512,
      desiredCount: 1,
      publicLoadBalancer: true,
      taskImageOptions: {
        image: ecs.ContainerImage.fromEcrRepository(
          ecr.Repository.fromRepositoryName(
            this,
            'FastApiRepoDev',
            this.node.tryGetContext('ecrRepository') ?? 'agentics/fastapi-dev'
          ),
          'latest'
        ),
        containerPort: 8001,
        environment: {
          LLM_PROVIDER: this.node.tryGetContext('llmProvider') ?? 'anthropic',
          BACKEND_HOST: 'localhost', // needed for container
          BACKEND_PORT: '8001', // needed for container
          POSTGRES_DB: 'postgres',
          POSTGRES_HOST: dbInstance.dbInstanceEndpointAddress,
          POSTGRES_PORT: dbInstance.dbInstanceEndpointPort.toString(),
          DEFAULT_AWS_REGION: 'us-west-2',
          MAX_TOKENS: '8000',
          REDIS_HOST: redisRG.attrPrimaryEndPointAddress,
          REDIS_PORT: redisRG.attrPrimaryEndPointPort,
          REDIS_TTL: '60',
          REDIS_TIMEOUT: '45',
        },
        secrets: {
          // Only the provider in use needs a real secret; create whichever one
          // matches LLM_PROVIDER before deploying.
          ANTHROPIC_API_KEY: ecs.Secret.fromSecretsManager(
            secretsmanager.Secret.fromSecretNameV2(this, 'ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY')
          ),
          POSTGRES_PASSWORD: ecs.Secret.fromSecretsManager(dbInstance.secret!, 'password'),
          POSTGRES_USER: ecs.Secret.fromSecretsManager(dbInstance.secret!, 'username'),
        },
      },
    });
    // 2. Auto scale task count
    const scalable = fastApiService.service.autoScaleTaskCount({
      minCapacity: 1,
      maxCapacity: 10,
    });
    scalable.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: 70,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });
    
    // Allow HTTPS traffic to the load balancer
    fastApiService.loadBalancer.connections.allowFromAnyIpv4(ec2.Port.tcp(443), 'Allow HTTPS');
    
    // IAM roles — least privilege. CDK already grants the execution role ECR pull,
    // CloudWatch Logs and read on the secrets referenced above, so it needs nothing
    // added here. The task role gets only what the app actually calls.
    // CognitoService.sanity_check() calls ListUserPools, which is not scopeable to
    // a single pool. GetUser is authorized by the caller's access token, not IAM.
    fastApiService.taskDefinition.taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        actions: ['cognito-idp:ListUserPools'],
        resources: ['*'],
      })
    );
    // #########################################################
    // STITCH SERVICES TOGETHER
    // #########################################################
    // Allow ECS service to connect to RDS
    dbInstance.connections.allowFrom(fastApiService.service, ec2.Port.tcp(8002));
    // Allow your ECS service to connect to Redis on port 6379
    redisSG.addIngressRule(
      fastApiService.service.connections.securityGroups[0],
      ec2.Port.tcp(6379),
      'ecs-to-redis'
    );
    // #########################################################
    // ADD HTTPS LISTENER TO LOAD BALANCER
    // #########################################################
    // Optional: pass an ACM certificate ARN to serve the API over HTTPS on a custom domain,
    // e.g. `npx cdk deploy AgenticsDevStack -c certificateArn=arn:aws:acm:...`.
    // Without it the stack still deploys and the API is reachable over HTTP at the ALB DNS
    // name emitted below.
    const certificateArn = this.node.tryGetContext('certificateArn');
    if (certificateArn) {
      const certificate = certificatemanager.Certificate.fromCertificateArn(
        this,
        'ApiCertificateDev',
        certificateArn
      );

      fastApiService.loadBalancer.addListener('HttpsListener', {
        port: 443,
        certificates: [certificate],
        defaultTargetGroups: [fastApiService.targetGroup],
      });
    }


    // #########################################################
    // OUTPUTS for other services to use (github actions, Amplify, etc.)
    // #########################################################
    new CfnOutput(this, 'FastApiEcsClusterNameDevOutput', {
      value: cluster.clusterName,
      exportName: 'FastApiEcsClusterNameDev',
    });

    new CfnOutput(this, 'FastApiServiceNameDevOutput', {
      value: fastApiService.service.serviceName,
      exportName: 'FastApiServiceNameDev',
    });

    new CfnOutput(this, 'FastApiLoadBalancerDNSDev', {
      value: fastApiService.loadBalancer.loadBalancerDnsName,
      exportName: 'FastApiLoadBalancerDNSDev',
    });
    
    
    
  }
}

#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AgenticsDevStack } from '../lib/dev-stack';
import { AgenticsProdStack } from '../lib/prod-stack';

const app = new cdk.App();

// Region comes from `-c region=...`, then CDK_DEFAULT_REGION, then a us-west-2 default.
// Account always comes from the ambient credentials — never hardcode one here.
const region =
  app.node.tryGetContext('region') ?? process.env.CDK_DEFAULT_REGION ?? 'us-west-2';
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };

new AgenticsDevStack(app, 'AgenticsDevStack', { env });
new AgenticsProdStack(app, 'AgenticsProdStack', { env });

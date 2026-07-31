import { defineBackend } from '@aws-amplify/backend';
import { auth } from './auth/resource.js';
import { customMessage } from './functions/customInviteMsg/resource.js';

const backend = defineBackend({
  auth,
  customMessage,
});

// overrides for user pool
const { cfnUserPool } = backend.auth.resources.cfnResources;

// block self sign up
cfnUserPool.adminCreateUserConfig = {
    allowAdminCreateUserOnly: true
  };

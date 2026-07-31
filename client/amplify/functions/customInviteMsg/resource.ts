import { defineFunction } from '@aws-amplify/backend';

export const customMessage = defineFunction({
  // optionally specify a name for the Function (defaults to directory name)
  name: 'custom-message',
  // optionally specify a path to your handler (defaults to "./handler.ts")
  entry: './handler.ts',
  //resourceGroupName: 'auth'
  
});
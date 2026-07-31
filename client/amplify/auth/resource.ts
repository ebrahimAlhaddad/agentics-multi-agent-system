import { defineAuth } from "@aws-amplify/backend";
import { customMessage } from '../functions/customInviteMsg/resource';

/**
 * Define and configure your auth resource
 * @see https://docs.amplify.aws/gen2/build-a-backend/auth
 */
export const auth = defineAuth({
  loginWith: {
    email: {
      verificationEmailStyle: "CODE",
      verificationEmailSubject: "Welcome to Agentics",
      verificationEmailBody: (createCode) => `Use this code to confirm your account: ${createCode()}`,
      userInvitation: {
        emailSubject: "You've been invited to Agentics",
        emailBody: (user, code) =>
          `We're happy to have you! You can now login with username ${user()} and temporary password ${code()}`, 
      },
    },
  },
  triggers: {
    customMessage
  },
});

import type { CustomMessageTriggerHandler } from "aws-lambda";

// Set APP_URL in the Amplify console to link invitees straight to the deployed app.
// Left unset, the invite email simply omits the link.
const APP_URL = process.env.APP_URL;

export const handler: CustomMessageTriggerHandler = async (event) => {
  if (event.triggerSource === 'CustomMessage_AdminCreateUser') {
    const accessMessage = APP_URL
      ? `<p>You can sign in at <a href="${APP_URL}">${APP_URL}</a></p>`
      : `<p>You will be contacted with a link to the app.</p>`;
    event.response.emailSubject = "You've been invited to Agentics";
    event.response.emailMessage = `
      <html>
        <body>
          <p>Welcome to Agentics!</p>
          <p>Your username is: <strong>${event.request.usernameParameter}</strong></p>
          <p>Your temporary password is: <strong>${event.request.codeParameter}</strong></p>
          <p>Please log in and change your password to activate your account.</p>
          ${accessMessage}
        </body>
      </html>
    `;
  }
  return event;
};

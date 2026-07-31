import { signOut } from "aws-amplify/auth";
import { amplifyConfigured } from "@/lib/amplify";

/**
 * SignOutButton - A reusable button to sign the user out using AWS Amplify Auth.
 * Place this component anywhere in your app where you want to provide a sign out option.
 */
export default function SignOutButton() {
  // Without real Amplify outputs there is no session to end, so render nothing
  // rather than a button that throws on click.
  if (!amplifyConfigured) return null;

  async function handleSignOut() {
    await signOut();
  }

  return (
    <button
      type="button"
      onClick={handleSignOut}
      className="px-4 py-2 rounded-full bg-gradient-to-r from-blue-600 to-indigo-600 text-white font-semibold hover:from-indigo-600 hover:to-blue-600 transition-colors"
    >
      Sign out
    </button>
  );
} 
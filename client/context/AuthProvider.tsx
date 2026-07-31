// AuthProvider.tsx
//
// This provider wraps your app with AWS Amplify authentication.
//
// IMPORTANT:
// - Theming and layout for the Amplify Authenticator (login/signup UI) is handled SEPARATELY from the rest of your app.
// - The custom theme and component overrides below ONLY affect the authentication UI, not your main app pages.
// - Once authenticated, your app's layout and theming are controlled by your own components and styles.
//
// For more info on Amplify Authenticator theming: https://ui.docs.amplify.aws/react/theming/

'use client';

import { ReactNode } from 'react';
import {
  Authenticator,
  ThemeProvider,
  Theme,
  View,
  useTheme,
} from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';
import { amplifyConfigured } from '@/lib/amplify';

interface AuthProviderProps {
  children: ReactNode;
}

export function AuthProvider({ children }: AuthProviderProps) {
  // Reaching this component with auth enabled but no real Amplify outputs means
  // the app is misconfigured. Fail loudly here rather than letting the
  // Authenticator throw something opaque.
  if (!amplifyConfigured) {
    throw new Error(
      'Authentication is enabled but client/amplify_outputs.json is a stub. ' +
        'Run `npx ampx sandbox` to generate real outputs, or set ' +
        'NEXT_PUBLIC_DISABLE_AUTH=true to run without authentication.'
    );
  }

  // Get Amplify UI theme tokens
  const { tokens } = useTheme();
  // Custom theme for the Authenticator only (does NOT affect the rest of the app)
  // See: https://ui.docs.amplify.aws/react/theming/
  const customTheme: Theme = {
    name: 'custom-theme',
    tokens: {
      components: {
        // These keys are for demonstration; see Amplify docs for supported keys
        authenticator: {
          // Note: Only supported properties will be applied
          container: {
            backgroundColor: tokens.colors.background.primary,
            padding: tokens.space.large,
            margin: '0 auto',
            borderRadius: tokens.radii.large,
            boxShadow: tokens.shadows.medium,
            borderWidth: '1px',
            borderColor: '#d1d5db', // Light gray border
            borderStyle: 'solid',
            maxWidth: '400px',
          },
          form: {
            backgroundColor: tokens.colors.background.primary,
            padding: tokens.space.large,
            margin: '0 auto',
            borderRadius: tokens.radii.large,
            boxShadow: tokens.shadows.medium,
            borderWidth: '1px',
            borderColor: '#d1d5db', // Light gray border
            borderStyle: 'solid',
            maxWidth: '400px',
          },
        },
        button: {
          primary: {
            backgroundColor: '#7C3AED', // Dark violet
            color: tokens.colors.white,
            _hover: {
              backgroundColor: '#1D4ED8', // Tailwind blue-500
            },
          },
        },
      },
    },
  };

  return (
    // ThemeProvider here ONLY affects the Authenticator UI
    <ThemeProvider theme={customTheme}>
      <Authenticator
        hideSignUp={true}
        components={{
          Container({ children }) {
            return (
              <div className="bg-white border border-gray-300 rounded-lg shadow-md max-w-md mx-auto p-6">
                {children}
              </div>
            );
          },
          Form({ children }) {
            return (
              <div className="bg-gray-50 p-6 rounded-lg">
                {children}
              </div>
            );
          },
          Header() {
            return (
              <div className="text-center mb-8">
                <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
                  Agentics
                </h1>
              </div>
            );
          },
        }}
      >
        {/*
          children (your app) will be rendered here after authentication.
          The Authenticator UI and its theme do NOT affect your app's layout or theme.
        */}
        {children}
      </Authenticator>
    </ThemeProvider>
  );
}

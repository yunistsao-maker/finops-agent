import { Amplify } from 'aws-amplify';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { config } from './config';
import './styles.css';

Amplify.configure({
  Auth: {
    Cognito: {
      userPoolId: config.userPoolId,
      userPoolClientId: config.userPoolClientId,
      identityPoolId: config.identityPoolId,
      loginWith: { username: true, email: true },
    },
  },
});

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);

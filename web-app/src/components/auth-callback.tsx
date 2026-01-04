import { useEffect, useState, useRef } from "react";
import authService from "../services/auth-service";
import { API_BASE_URL } from "../configs/url-config";

const AuthCallback: React.FC = () => {
  const [status, setStatus] = useState<'loading' | 'error' | 'success'>('loading');
  const [error, setError] = useState<string>('');
  const [errorDetails, setErrorDetails] = useState<string>('');
  const hasInitiatedRef = useRef(false);

  useEffect(() => {
    const handleCallback = async () => {
      // Prevent double execution in React StrictMode
      if (hasInitiatedRef.current) {
        return;
      }
      hasInitiatedRef.current = true;
      const urlParams = new URLSearchParams(window.location.search);
      
      // Check if there's an error from Auth0
      if (urlParams.has('error')) {
        const errorCode = urlParams.get('error') || 'Unknown error';
        const errorDesc = urlParams.get('error_description') || 'Authentication failed';
        setError(errorDesc);
        setErrorDetails(`Error Code: ${errorCode}`);
        setStatus('error');
        return;
      }

      try {
        // Call the /auth endpoint which handles the OAuth callback
        const response = await fetch(`${API_BASE_URL}/auth${window.location.search}`, {
          credentials: 'include',
        });

        let data: any;
        try {
          data = await response.json();
        } catch {
          // If response is not JSON, treat it as an error
          const errorMessage = response.statusText || 'Authentication failed';
          console.error('Failed to parse response as JSON:', errorMessage, response.status);
          setError(errorMessage);
          setErrorDetails(`HTTP ${response.status}: Server returned non-JSON response`);
          setStatus('error');
          return;
        }

        console.log('Auth response received:', { status: response.status, data });

        // Check if the response contains an error (e.g., {"status_code":401,"detail":"access_denied"})
        if (!response.ok || data.status_code === 401 || data.status_code >= 400) {
          const errorMessage = data.detail || data.message || response.statusText || 'Authentication failed';
          const statusCode = data.status_code || response.status;
          console.error('Authentication error:', { errorMessage, statusCode });
          setError(errorMessage);
          setErrorDetails(`Error Code: ${statusCode}`);
          setStatus('error');
          return;
        }

        // Validate that we have the required fields
        if (!data.access_token || !data.user) {
          console.error('Missing required fields in response:', { hasToken: !!data.access_token, hasUser: !!data.user });
          setError('Invalid authentication response');
          setErrorDetails('Server did not return required authentication data');
          setStatus('error');
          return;
        }

        // Store the token and user info
        console.log('Storing token and user:', { email: data.user.email, name: data.user.name });
        authService.setToken(data.access_token);
        authService.setUser(data.user);
        console.log('Token and user stored successfully');
        
        setStatus('success');
        
        // Redirect to dashboard
        setTimeout(() => {
          window.location.href = '/';
        }, 1500);
      } catch (err) {
        const errorMsg = (err as Error).message;
        console.error('Unexpected error in auth callback:', errorMsg);
        setError('Failed to authenticate');
        setErrorDetails(errorMsg || 'An unexpected error occurred during authentication');
        setStatus('error');
      }
    };

    handleCallback();
  }, []);

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl p-8 max-w-md w-full">
        {status === 'loading' && (
          <div className="text-center">
            <div className="inline-block animate-spin rounded-full h-16 w-16 border-b-4 border-blue-600 mb-4"></div>
            <h2 className="text-xl font-semibold text-gray-800 mb-2">Authenticating...</h2>
            <p className="text-gray-600">Please wait while we sign you in</p>
          </div>
        )}
        
        {status === 'success' && (
          <div className="text-center">
            <div className="inline-block w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-green-600" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-gray-800 mb-2">Success!</h2>
            <p className="text-gray-600">Redirecting to dashboard...</p>
          </div>
        )}
        
        {status === 'error' && (
          <div className="text-center">
            <div className="inline-block w-16 h-16 bg-red-100 rounded-full flex items-center justify-center mb-4">
              <svg className="w-8 h-8 text-red-600" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
              </svg>
            </div>
            <h2 className="text-xl font-semibold text-gray-800 mb-2">Authentication Failed</h2>
            <p className="text-gray-600 mb-2 font-semibold">{error}</p>
            {errorDetails && (
              <div className="bg-gray-50 rounded p-3 mb-4 text-left">
                <p className="text-gray-700 text-xs font-mono">{errorDetails}</p>
              </div>
            )}
            <div className="flex gap-3">
              <button
                onClick={() => window.location.href = '/login'}
                className="flex-1 bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 transition-colors font-semibold"
              >
                Back to Login
              </button>
              <button
                onClick={() => window.location.href = '/'}
                className="flex-1 bg-gray-300 text-gray-800 px-4 py-2 rounded-lg hover:bg-gray-400 transition-colors font-semibold"
              >
                Home
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default AuthCallback;
import { useState } from 'react';
import { authApi } from '../services/auth-api';

const LoginPage: React.FC = () => {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleLogin = async () => {
    setIsLoading(true);
    setError(null);
    try {
      authApi.login();
    } catch (err) {
      setError((err as Error).message || 'Failed to initiate login. Please try again.');
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-500 via-purple-500 to-pink-500 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl p-8 max-w-md w-full transform hover:scale-105 transition-transform duration-300">
        <div className="text-center mb-8">
          <div className="inline-block p-4 bg-gradient-to-r from-blue-500 to-purple-600 rounded-full mb-4">
            <svg 
              className="w-12 h-12 text-white" 
              fill="none" 
              stroke="currentColor" 
              viewBox="0 0 24 24"
            >
              <path 
                strokeLinecap="round" 
                strokeLinejoin="round" 
                strokeWidth={2} 
                d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" 
              />
            </svg>
          </div>
          <h1 className="text-3xl font-bold text-gray-800 mb-2">Welcome Back</h1>
          <p className="text-gray-600">Sign in to access your dashboard</p>
        </div>

        {error && (
          <div className="mb-6 bg-red-50 border border-red-200 rounded-lg p-4">
            <p className="text-red-700 text-sm font-semibold mb-1">Login Failed</p>
            <p className="text-red-600 text-sm">{error}</p>
          </div>
        )}
        
        <button
          onClick={handleLogin}
          disabled={isLoading}
          className="w-full bg-gradient-to-r from-blue-600 to-purple-600 text-white font-semibold py-3 px-6 rounded-lg hover:from-blue-700 hover:to-purple-700 transition-all duration-300 shadow-lg hover:shadow-xl transform hover:-translate-y-1 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:from-blue-600 disabled:hover:to-purple-600 disabled:hover:translate-y-0 flex items-center justify-center"
        >
          {isLoading ? (
            <>
              <span className="animate-spin rounded-full h-5 w-5 border-b-2 border-white mr-2"></span>
              Signing in...
            </>
          ) : (
            'Login with Auth0'
          )}
        </button>
        
        <div className="mt-6 text-center text-sm text-gray-500">
          <p>Secure authentication powered by Auth0</p>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
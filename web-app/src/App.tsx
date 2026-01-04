import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AuthCallback from './components/auth-callback';
import LoginPage from './components/login-page';
import { Dashboard } from './components/dashboard';
import useAuth from './hooks/use-auth';

const App = () => {
  const { user, isLoading } = useAuth();

  if (isLoading) return <div className="spinner" />;

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={!user ? <LoginPage /> : <Navigate to="/" />} />
        <Route path="/auth" element={<AuthCallback />} />
        
        <Route path="/" element={user ? <Dashboard /> : <Navigate to="/login" />} />
      </Routes>
    </BrowserRouter>
  );
};

export default App;
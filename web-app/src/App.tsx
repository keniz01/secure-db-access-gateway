import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AuthCallback from './components/auth-callback';
import LoginPage from './components/login-page';
import useAuth from './hooks/use-auth';

const Dashboard = lazy(() =>
  import('./components/dashboard').then(({ Dashboard: component }) => ({ default: component }))
);

const App = () => {
  const { user, isLoading } = useAuth();

  if (isLoading) return <div className="spinner" />;

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={!user ? <LoginPage /> : <Navigate to="/" />} />
        <Route path="/auth" element={<AuthCallback />} />

        <Route
          path="/"
          element={
            user ? (
              <Suspense fallback={<div className="spinner" />}>
                <Dashboard />
              </Suspense>
            ) : (
              <Navigate to="/login" />
            )
          }
        />
      </Routes>
    </BrowserRouter>
  );
};

export default App;

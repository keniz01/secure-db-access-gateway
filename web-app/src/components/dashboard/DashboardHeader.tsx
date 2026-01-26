interface DashboardHeaderProps {
  onLogout: () => void;
}

export const DashboardHeader = ({ onLogout }: DashboardHeaderProps) => {
  return (
    <header className="bg-white shadow-md">
      <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-800">SQL Query Dashboard</h1>
        <button
          onClick={onLogout}
          className="px-6 py-2 bg-red-600 hover:bg-red-700 text-white font-semibold rounded-lg transition-colors duration-200 shadow-md hover:shadow-lg"
        >
          Logout
        </button>
      </div>
    </header>
  );
};
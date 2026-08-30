import type { User } from '../../models/user-profile';

interface UserInfoCardProps {
  user: User | null;
  dashboardMessage?: string;
}

export const UserInfoCard = ({ user, dashboardMessage }: UserInfoCardProps) => {
  return (
    <div className="bg-white rounded-2xl shadow-lg p-8 mb-8">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center space-x-4">
          <div className="w-16 h-16 bg-gradient-to-br from-indigo-500 to-purple-600 rounded-full flex items-center justify-center flex-shrink-0">
            <span className="text-2xl font-bold text-white">
              {user?.name?.charAt(0)?.toUpperCase() || 'U'}
            </span>
          </div>
          <div>
            <h2 className="text-3xl font-bold text-gray-800">Welcome, {user?.name}!</h2>
            <p className="text-gray-600">{user?.email}</p>
            {dashboardMessage && (
              <p className="text-green-600 text-sm mt-1">{dashboardMessage.replace(/"/g, '')}</p>
            )}
          </div>
        </div>
        <div className="inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold bg-indigo-100 text-indigo-700">
          Role: {user?.role || 'viewer'}
        </div>
      </div>
    </div>
  );
};
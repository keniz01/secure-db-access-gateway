import { useState, useMemo } from 'react';

interface ResultsTableProps {
  data: Record<string, unknown>[];
  rowsPerPage?: number;
}

const ROWS_PER_PAGE = 15;

export const ResultsTable = ({ data, rowsPerPage = ROWS_PER_PAGE }: ResultsTableProps) => {
  const [currentPage, setCurrentPage] = useState(1);

  const columns = useMemo(() => {
    if (!data || data.length === 0) return [];
    return Object.keys(data[0]);
  }, [data]);

  const totalPages = Math.ceil(data.length / rowsPerPage);
  const startIndex = (currentPage - 1) * rowsPerPage;
  const endIndex = startIndex + rowsPerPage;
  const currentData = data.slice(startIndex, endIndex);

  const goToPage = (page: number) => {
    setCurrentPage(Math.max(1, Math.min(page, totalPages)));
  };

  if (!data || data.length === 0) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-500 text-lg">No data to display</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="mb-4 text-gray-600 text-sm">
        <p>
          Showing {startIndex + 1}-{Math.min(endIndex, data.length)} of {data.length} row(s)
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-gray-100 border-b-2 border-gray-300">
              {columns.map((key) => (
                <th
                  key={key}
                  className="px-4 py-3 text-left text-sm font-semibold text-gray-700"
                >
                  {key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {currentData.map((row: Record<string, unknown>, idx: number) => (
              <tr
                key={startIndex + idx}
                className={`border-b border-gray-200 hover:bg-gray-50 ${
                  (startIndex + idx) % 2 === 0 ? 'bg-white' : 'bg-gray-50'
                }`}
              >
                {Object.values(row).map((val: unknown, valIdx: number) => (
                  <td
                    key={valIdx}
                    className="px-4 py-3 text-sm text-gray-700 font-mono"
                  >
                    {val !== null && val !== undefined ? String(val) : 'NULL'}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-6">
          <div className="flex items-center space-x-2">
            <button
              onClick={() => goToPage(currentPage - 1)}
              disabled={currentPage === 1}
              className="px-3 py-2 text-sm font-medium text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Previous
            </button>

            <div className="flex items-center space-x-1">
              {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                const pageNum = Math.max(1, Math.min(totalPages - 4, currentPage - 2)) + i;
                if (pageNum > totalPages) return null;

                return (
                  <button
                    key={pageNum}
                    onClick={() => goToPage(pageNum)}
                    className={`px-3 py-2 text-sm font-medium rounded-md ${
                      pageNum === currentPage
                        ? 'text-indigo-600 bg-indigo-50 border border-indigo-300'
                        : 'text-gray-500 bg-white border border-gray-300 hover:bg-gray-50'
                    }`}
                  >
                    {pageNum}
                  </button>
                );
              })}
            </div>

            <button
              onClick={() => goToPage(currentPage + 1)}
              disabled={currentPage === totalPages}
              className="px-3 py-2 text-sm font-medium text-gray-500 bg-white border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>

          <div className="text-sm text-gray-500">
            Page {currentPage} of {totalPages}
          </div>
        </div>
      )}
    </div>
  );
};
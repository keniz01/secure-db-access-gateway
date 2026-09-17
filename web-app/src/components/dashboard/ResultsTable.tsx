import { useState, useMemo } from 'react';
import { humanizeColumn } from '../../utils/column-labels';

interface ResultsTableProps {
  data: Record<string, unknown>[];
  rowsPerPage?: number;
}

const ROWS_PER_PAGE = 15;

// An array cell (e.g. array_agg) is unreadable as a single comma-joined blob;
// cap the per-line items so a pathological cell cannot blow out the layout.
const MAX_ARRAY_CELL_ITEMS = 25;

const formatCellValue = (val: unknown): string => {
  if (val !== null && val !== undefined && typeof val === 'object' && !Array.isArray(val)) {
    return JSON.stringify(val);
  }
  return String(val ?? '');
};

const formatArrayCell = (items: unknown[]): { shown: string[]; hidden: number } => {
  const shown = items.slice(0, MAX_ARRAY_CELL_ITEMS).map((item) => String(item));
  return { shown, hidden: items.length - shown.length };
};

const addWrapHints = (value: string): string =>
  value.length > 40 ? value.replace(/,(?=[^ ])/g, ',\u200B') : value;

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
                  {humanizeColumn(key)}
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
                {Object.values(row).map((val: unknown, valIdx: number) => {
                  const isNull = val === null || val === undefined;
                  const isArray = Array.isArray(val);
                  const { shown, hidden } = isArray
                    ? formatArrayCell(val as unknown[])
                    : { shown: [], hidden: 0 };
                  return (
                    <td
                      key={valIdx}
                      className="px-4 py-3 text-sm text-gray-700 font-mono whitespace-pre-wrap break-words align-top"
                    >
                      {isNull ? (
                        <span className="text-gray-400 italic">NULL</span>
                      ) : isArray ? (
                        <>
                          {shown.map((item, itemIdx) => (
                            <span key={itemIdx} className="block whitespace-normal break-words">
                              {item}
                            </span>
                          ))}
                          {hidden > 0 && (
                            <span className="block text-gray-400 italic">… {hidden} more</span>
                          )}
                        </>
                      ) : (
                        addWrapHints(formatCellValue(val))
                      )}
                    </td>
                  );
                })}
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
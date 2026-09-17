interface ListRendererProps {
  content: string;
}

const normalizeItems = (content: string): string[] => {
  const lines = content
    .split(/(?:\r?\n)+/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (lines.length <= 1) {
    return lines[0]
      ? lines[0]
          .split(/[,;]\s+/)
          .map((item) => item.trim())
          .filter(Boolean)
      : [];
  }
  return lines;
};

export const ListRenderer = ({ content }: ListRendererProps): React.JSX.Element => {
  const items = normalizeItems(content);

  return (
    <div className="bg-gray-50 border-l-4 border-indigo-400 p-6 rounded-lg">
      <ul className="space-y-2">
        {items.map((item, idx) => (
          <li key={idx} className="text-gray-800">
            <span className="inline-block w-4 mr-2 text-indigo-600">•</span>
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
};
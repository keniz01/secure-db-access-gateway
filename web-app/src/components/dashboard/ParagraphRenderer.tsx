interface ParagraphRendererProps {
  content: string;
}

export const ParagraphRenderer = ({ content }: ParagraphRendererProps): React.JSX.Element => (
  <div className="bg-gray-50 border-l-4 border-indigo-400 p-6 rounded-lg">
    <p className="text-gray-800 leading-relaxed">{content}</p>
  </div>
);
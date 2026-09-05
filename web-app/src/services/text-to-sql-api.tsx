import { API_BASE_URL, DEFAULT_DATABASE_ID } from '../configs/url-config';
import apiClient from './api-client';

interface TextToSqlRequest {
  query: string;
  execute?: boolean;
  database_id: string;
}

interface TextToSqlResponse {
  sql: string;
  results?: Record<string, unknown>[];
  schema?: string;
  error?: string;
}

export const textToSqlApi = {
  generateSql: async (
    query: string,
    execute: boolean = false,
    databaseId: string = DEFAULT_DATABASE_ID
  ): Promise<TextToSqlResponse> => {
    try {
      const { data } = await apiClient.post<TextToSqlResponse>(
        `${API_BASE_URL}/api/text-to-sql`,
        { query, execute, database_id: databaseId } as TextToSqlRequest
      );
      return data;
    } catch (error) {
      const axiosError = error as { response?: { data?: { detail?: string } } };
      const message = axiosError.response?.data?.detail || (error as Error).message || 'Failed to generate SQL';
      throw new Error(message);
    }
  },
};

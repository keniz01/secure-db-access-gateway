import { API_BASE_URL } from '../configs/url-config';
import apiClient from './api-client';

interface TextToSqlRequest {
  query: string;
  execute?: boolean;
}

interface TextToSqlResponse {
  sql: string;
  results?: Record<string, unknown>[];
  schema?: string;
  error?: string;
}

export const textToSqlApi = {
  generateSql: async (query: string, execute: boolean = false): Promise<TextToSqlResponse> => {
    try {
      const { data } = await apiClient.post<TextToSqlResponse>(
        `${API_BASE_URL}/api/text-to-sql`,
        { query, execute } as TextToSqlRequest
      );
      return data;
    } catch (error) {
      const axiosError = error as { response?: { data?: { detail?: string } } };
      const message = axiosError.response?.data?.detail || (error as Error).message || 'Failed to generate SQL';
      throw new Error(message);
    }
  },
};


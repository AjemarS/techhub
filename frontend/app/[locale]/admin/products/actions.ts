import { productApi } from "~/lib/api/admin-api";
import type { Product } from "~/lib/types";

export const productService = {
  async list(params?: Record<string, string | number | boolean | undefined>) {
    return productApi.getAll(params);
  },
  async getById(id: number) {
    return productApi.getById(id);
  },
  async create(data: Partial<Product>) {
    return productApi.create(data);
  },
  async update(id: number, data: Partial<Product>) {
    return productApi.update(id, data);
  },
  async remove(id: number) {
    return productApi.delete(id);
  },
  async getLowStock(threshold?: number) {
    return productApi.getLowStock(threshold);
  },
};

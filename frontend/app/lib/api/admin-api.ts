import type { Product, Category, Warehouse, Stock, StockMovement, Supplier, GoodsReceiptNote, Order, OrderDetail, SalesReport, RevenueReport, ActivityEvent, SupplierStats, SalesGroup, SalesSeries, TopProduct, TopProductsSort, AbcReport, StockAnalytics, FulfillmentReport, ReturnRequest, ReturnKind, ReturnStatus, ReturnResolution, Review, ReviewsPage, PurchaseOrder, PurchaseOrderItem, PurchaseOrderStatus, ReorderSuggestion, PromoCode } from "~/lib/types";
import { apiCall, API_URL, INVENTORY_API_URL, ORDERS_API_URL } from "./client";
import type { ApiResponse } from "./client";

export const productApi = {
  async getAll(params?: {
    page?: number;
    pageSize?: number;
    search?: string;
    ids?: string;
    category?: number;
    minPrice?: number;
    maxPrice?: number;
    inStock?: boolean;
    minStock?: number;
    maxStock?: number;
    ordering?: string;
    createdAfter?: string;
    createdBefore?: string;
    minRating?: number;
    onSale?: boolean;
    brand?: string;
    color?: string;
  }): Promise<ApiResponse<{ results: Product[]; count: number; next: string | null; previous: string | null }>> {
    const queryParams = new URLSearchParams();
    if (params?.page) queryParams.append("page", params.page.toString());
    if (params?.pageSize) queryParams.append("page_size", params.pageSize.toString());
    if (params?.search) queryParams.append("search", params.search);
    if (params?.ids) queryParams.append("ids", params.ids);
    if (params?.category) queryParams.append("category", params.category.toString());
    if (params?.minPrice) queryParams.append("min_price", params.minPrice.toString());
    if (params?.maxPrice) queryParams.append("max_price", params.maxPrice.toString());
    if (params?.inStock !== undefined) queryParams.append("in_stock", params.inStock.toString());
    if (params?.minRating !== undefined) queryParams.append("min_rating", params.minRating.toString());
    if (params?.onSale !== undefined) queryParams.append("on_sale", params.onSale.toString());
    if (params?.brand) queryParams.append("brand", params.brand);
    if (params?.color) queryParams.append("color", params.color);
    if (params?.minStock !== undefined) queryParams.append("min_stock", params.minStock.toString());
    if (params?.maxStock !== undefined) queryParams.append("max_stock", params.maxStock.toString());
    if (params?.ordering) queryParams.append("ordering", params.ordering);
    if (params?.createdAfter) queryParams.append("created_after", params.createdAfter);
    if (params?.createdBefore) queryParams.append("created_before", params.createdBefore);
    const url = `${API_URL}/products/${queryParams.toString() ? `?${queryParams}` : ""}`;
    return apiCall(url);
  },

  async getById(id: number): Promise<ApiResponse<Product>> {
    return apiCall(`${API_URL}/products/${id}/`);
  },

  async create(product: Partial<Product>): Promise<ApiResponse<Product>> {
    return apiCall(`${API_URL}/products/`, { method: "POST", body: JSON.stringify(product) });
  },

  async update(id: number, product: Partial<Product>): Promise<ApiResponse<Product>> {
    return apiCall(`${API_URL}/products/${id}/`, { method: "PUT", body: JSON.stringify(product) });
  },

  async delete(id: number): Promise<ApiResponse<void>> {
    return apiCall(`${API_URL}/products/${id}/`, { method: "DELETE" });
  },

  async getSimilar(id: number): Promise<ApiResponse<Product[]>> {
    return apiCall(`${API_URL}/products/${id}/similar/`);
  },

  async updateStock(id: number, quantity: number): Promise<ApiResponse<Product>> {
    return apiCall(`${API_URL}/products/${id}/update_stock/`, {
      method: "POST", body: JSON.stringify({ quantity }),
    });
  },

  async getLowStock(threshold = 10): Promise<ApiResponse<Product[]>> {
    return apiCall(`${API_URL}/products/low_stock/?threshold=${threshold}`);
  },
};

export const categoryApi = {
  async getAll(params?: {
    page?: number;
    pageSize?: number;
    search?: string;
    parent?: number;
  }): Promise<ApiResponse<{ results: Category[]; count: number; next: string | null; previous: string | null }>> {
    const queryParams = new URLSearchParams();
    if (params?.page) queryParams.append("page", params.page.toString());
    if (params?.pageSize) queryParams.append("page_size", params.pageSize.toString());
    if (params?.search) queryParams.append("search", params.search);
    if (params?.parent !== undefined) queryParams.append("parent", params.parent.toString());
    const url = `${API_URL}/categories/${queryParams.toString() ? `?${queryParams}` : ""}`;
    return apiCall(url);
  },

  async getById(id: number): Promise<ApiResponse<Category>> {
    return apiCall(`${API_URL}/categories/${id}/`);
  },

  async create(category: Partial<Category>): Promise<ApiResponse<Category>> {
    return apiCall(`${API_URL}/categories/`, { method: "POST", body: JSON.stringify(category) });
  },

  async update(id: number, category: Partial<Category>): Promise<ApiResponse<Category>> {
    return apiCall(`${API_URL}/categories/${id}/`, { method: "PUT", body: JSON.stringify(category) });
  },

  async delete(id: number): Promise<ApiResponse<void>> {
    return apiCall(`${API_URL}/categories/${id}/`, { method: "DELETE" });
  },
};

export const warehouseApi = {
  async getAll(): Promise<ApiResponse<{ results: Warehouse[]; count: number; next: string | null; previous: string | null }>> {
    return apiCall(`${INVENTORY_API_URL}/warehouses/`);
  },

  async create(data: Partial<Warehouse>): Promise<ApiResponse<Warehouse>> {
    return apiCall(`${INVENTORY_API_URL}/warehouses/`, { method: "POST", body: JSON.stringify(data) });
  },

  async update(id: number, data: Partial<Warehouse>): Promise<ApiResponse<Warehouse>> {
    return apiCall(`${INVENTORY_API_URL}/warehouses/${id}/`, { method: "PUT", body: JSON.stringify(data) });
  },

  async delete(id: number): Promise<ApiResponse<void>> {
    return apiCall(`${INVENTORY_API_URL}/warehouses/${id}/`, { method: "DELETE" });
  },
};

export const stockApi = {
  async getAll(params?: { warehouse_id?: number; product_id?: number; pageSize?: number }, signal?: AbortSignal): Promise<ApiResponse<Stock[]>> {
    const q = new URLSearchParams();
    if (params?.warehouse_id) q.append("warehouse_id", String(params.warehouse_id));
    if (params?.product_id) q.append("product_id", String(params.product_id));
    if (params?.pageSize) q.append("page_size", String(params.pageSize));
    const qs = q.toString();
    return apiCall(`${INVENTORY_API_URL}/stock/${qs ? `?${qs}` : ""}`, { signal });
  },

  async setReorder(stockId: number, data: { reorder_point: number; reorder_quantity: number }): Promise<ApiResponse<Stock>> {
    return apiCall(`${INVENTORY_API_URL}/stock/${stockId}/reorder/`, { method: "PATCH", body: JSON.stringify(data) });
  },

  async reorderSuggestions(warehouseId?: number): Promise<ApiResponse<ReorderSuggestion[]>> {
    return apiCall(`${INVENTORY_API_URL}/stock/reorder-suggestions/${warehouseId ? `?warehouse_id=${warehouseId}` : ""}`);
  },

  async analytics(days = 90): Promise<ApiResponse<StockAnalytics>> {
    return apiCall(`${INVENTORY_API_URL}/stock/analytics/?days=${days}`);
  },
};

export const supplierApi = {
  async getAll(): Promise<ApiResponse<{ results: Supplier[]; count: number }>> {
    return apiCall(`${INVENTORY_API_URL}/suppliers/`);
  },

  async create(data: Partial<Supplier>): Promise<ApiResponse<Supplier>> {
    return apiCall(`${INVENTORY_API_URL}/suppliers/`, { method: "POST", body: JSON.stringify(data) });
  },

  async update(id: number, data: Partial<Supplier>): Promise<ApiResponse<Supplier>> {
    return apiCall(`${INVENTORY_API_URL}/suppliers/${id}/`, { method: "PUT", body: JSON.stringify(data) });
  },

  async delete(id: number): Promise<ApiResponse<void>> {
    return apiCall(`${INVENTORY_API_URL}/suppliers/${id}/`, { method: "DELETE" });
  },
};

export const goodsReceiptApi = {
  async getAll(): Promise<ApiResponse<{ results: GoodsReceiptNote[]; count: number }>> {
    return apiCall(`${INVENTORY_API_URL}/goods-receipts/`);
  },

  async supplierStats(): Promise<ApiResponse<SupplierStats[]>> {
    return apiCall(`${INVENTORY_API_URL}/goods-receipts/supplier-stats/`);
  },

  async productInfo(product_id: number, warehouse_id: number): Promise<ApiResponse<{ product_id: number; warehouse_id: number; last_cost_price: string | null; current_stock: number }>> {
    return apiCall(`${INVENTORY_API_URL}/goods-receipts/product_info/?product_id=${product_id}&warehouse_id=${warehouse_id}`);
  },

  async create(data: Partial<GoodsReceiptNote>): Promise<ApiResponse<GoodsReceiptNote>> {
    return apiCall(`${INVENTORY_API_URL}/goods-receipts/`, { method: "POST", body: JSON.stringify(data) });
  },

};

export const orderApi = {
  async create(data: {
    channel?: string;
    warehouse_id?: number;
    delivery_method?: string;
    shipping_city?: string;
    shipping_address?: string;
    customer_name?: string;
    customer_phone?: string;
    customer_email?: string;
    notes?: string;
    promo_code?: string;
    items: { product_id: number; product_name?: string; quantity: number; price: number }[];
  }): Promise<ApiResponse<OrderDetail>> {
    return apiCall(`${ORDERS_API_URL}/`, { method: "POST", body: JSON.stringify(data) });
  },

  async checkPromo(code: string, subtotal: number): Promise<ApiResponse<{ code: string; kind: string; value: string; discount: string; total: string }>> {
    const q = new URLSearchParams({ code, subtotal: subtotal.toFixed(2) });
    return apiCall(`${ORDERS_API_URL}/promo-check/?${q}`);
  },

  /** token: the pay_token from order creation, needed by guests (signed-in buyers are recognised) */
  async pay(id: number, token?: string): Promise<ApiResponse<{ checkout_url: string; session_id: string }>> {
    return apiCall(`${ORDERS_API_URL}/${id}/pay/`, { method: "POST", body: JSON.stringify(token ? { token } : {}) });
  },

  async getAll(params?: { page?: number; status?: string; channel?: string; search?: string; ordering?: string }): Promise<
    ApiResponse<{ results: Order[]; count: number; next: string | null; previous: string | null }>
  > {
    const q = new URLSearchParams();
    if (params?.page) q.append("page", String(params.page));
    if (params?.status) q.append("status", params.status);
    if (params?.channel) q.append("channel", params.channel);
    if (params?.search) q.append("search", params.search);
    if (params?.ordering) q.append("ordering", params.ordering);
    return apiCall(`${ORDERS_API_URL}/${q.toString() ? `?${q}` : ""}`);
  },

  async getById(id: number): Promise<ApiResponse<OrderDetail>> {
    return apiCall(`${ORDERS_API_URL}/${id}/`);
  },

  async updateStatus(id: number, status: string): Promise<ApiResponse<OrderDetail>> {
    return apiCall(`${ORDERS_API_URL}/${id}/status/`, {
      method: "PATCH", body: JSON.stringify({ status }),
    });
  },

  async getMy(): Promise<ApiResponse<{ results: Order[]; count: number }>> {
    return apiCall(`${ORDERS_API_URL}/my/`);
  },

  async pos(data: {
    warehouse_id: number; customer_name?: string; customer_phone?: string;
    items: { product_id: number; quantity: number; price: number }[];
  }): Promise<ApiResponse<OrderDetail>> {
    return apiCall(`${ORDERS_API_URL}/pos/`, { method: "POST", body: JSON.stringify(data) });
  },

  async cancel(id: number): Promise<ApiResponse<OrderDetail>> {
    return apiCall(`${ORDERS_API_URL}/${id}/cancel/`, { method: "POST" });
  },
};

export const stockMovementApi = {
  async getAll(params?: {
    product_id?: number;
    type?: string;
    from_warehouse_id?: number;
    to_warehouse_id?: number;
    created_after?: string;
    created_before?: string;
    page?: number;
  }): Promise<ApiResponse<{ results: StockMovement[]; count: number; next: string | null; previous: string | null }>> {
    const q = new URLSearchParams();
    if (params?.product_id) q.append("product_id", String(params.product_id));
    if (params?.type) q.append("type", params.type);
    if (params?.from_warehouse_id) q.append("from_warehouse_id", String(params.from_warehouse_id));
    if (params?.to_warehouse_id) q.append("to_warehouse_id", String(params.to_warehouse_id));
    if (params?.created_after) q.append("created_after", params.created_after);
    if (params?.created_before) q.append("created_before", params.created_before);
    if (params?.page) q.append("page", String(params.page));
    return apiCall(`${INVENTORY_API_URL}/stock/movements/${q.toString() ? `?${q}` : ""}`);
  },
};

export const stockTransferApi = {
  async transfer(data: {
    product_id: number;
    from_warehouse_id: number;
    to_warehouse_id: number;
    quantity: number;
    reference_type?: string;
    reference_id?: string;
    notes?: string;
  }): Promise<ApiResponse<{ message: string; movements: { from: number; to: number } }>> {
    return apiCall(`${INVENTORY_API_URL}/stock/transfer/`, { method: "POST", body: JSON.stringify(data) });
  },
};

export const stockAdjustApi = {
  async adjust(data: {
    product_id: number;
    warehouse_id: number;
    new_quantity: number;
    reason?: string;
  }): Promise<ApiResponse<unknown>> {
    return apiCall(`${INVENTORY_API_URL}/stock/adjust/`, { method: "POST", body: JSON.stringify(data) });
  },
};

export const reportApi = {
  async sales(from?: string, to?: string): Promise<ApiResponse<SalesReport>> {
    const q = new URLSearchParams();
    if (from) q.append("from", from);
    if (to) q.append("to", to);
    const url = `${API_URL}/reports/sales/${q.toString() ? `?${q}` : ""}`;
    return apiCall(url);
  },

  async revenue(from?: string, to?: string): Promise<ApiResponse<RevenueReport>> {
    const q = new URLSearchParams();
    if (from) q.append("from", from);
    if (to) q.append("to", to);
    const url = `${API_URL}/reports/revenue/${q.toString() ? `?${q}` : ""}`;
    return apiCall(url);
  },

  async abc(from?: string, to?: string): Promise<ApiResponse<AbcReport>> {
    const q = new URLSearchParams();
    if (from) q.append("from", from);
    if (to) q.append("to", to);
    return apiCall(`${API_URL}/reports/abc/${q.toString() ? `?${q}` : ""}`);
  },

  async fulfillment(from?: string, to?: string): Promise<ApiResponse<FulfillmentReport>> {
    const q = new URLSearchParams();
    if (from) q.append("from", from);
    if (to) q.append("to", to);
    return apiCall(`${API_URL}/reports/fulfillment/${q.toString() ? `?${q}` : ""}`);
  },

  async inventoryValue(): Promise<ApiResponse<{ total_value: number; item_count: number }>> {
    return apiCall(`${API_URL}/reports/inventory-value/`);
  },

  async dailySales(params?: { from?: string; to?: string; group?: SalesGroup }): Promise<ApiResponse<SalesSeries>> {
    const q = new URLSearchParams();
    if (params?.from) q.append("from", params.from);
    if (params?.to) q.append("to", params.to);
    if (params?.group) q.append("group", params.group);
    return apiCall(`${API_URL}/reports/daily-sales/${q.toString() ? `?${q}` : ""}`);
  },

  async topProducts(params?: { from?: string; to?: string; sort?: TopProductsSort; limit?: number }): Promise<ApiResponse<{ sort: TopProductsSort; products: TopProduct[] }>> {
    const q = new URLSearchParams();
    if (params?.from) q.append("from", params.from);
    if (params?.to) q.append("to", params.to);
    if (params?.sort) q.append("sort", params.sort);
    if (params?.limit) q.append("limit", String(params.limit));
    return apiCall(`${API_URL}/reports/top-products/${q.toString() ? `?${q}` : ""}`);
  },
};

export const activityEventApi = {
  async list(): Promise<ApiResponse<ActivityEvent[]>> {
    return apiCall(`${INVENTORY_API_URL}/activity/events/`);
  },

  async create(data: {
    event_type: string;
    message: string;
    entity_type: string;
    entity_id?: string;
  }): Promise<ApiResponse<ActivityEvent>> {
    return apiCall(`${INVENTORY_API_URL}/activity/events/`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },
};

export interface AuditLogEntry {
  id: number;
  event_type: "rabbitmq" | "http_request";
  routing_key: string | null;
  service: string;
  method: string | null;
  path: string | null;
  user_id: string | null;
  status_code: number | null;
  created_at: string;
}

export const auditApi = {
  async list(params: { page: number; user_id?: string; event_type?: string; routing_key?: string; from?: string; to?: string }): Promise<ApiResponse<{ results: AuditLogEntry[]; total: number }>> {
    const q = new URLSearchParams({ page: String(params.page), limit: "50" });
    for (const key of ["user_id", "event_type", "routing_key", "from", "to"] as const) {
      if (params[key]) q.append(key, params[key]);
    }
    return apiCall(`${API_URL}/audit/logs?${q}`);
  },
};

export const returnsApi = {
  async list(params?: { status?: ReturnStatus; order?: number; page?: number }): Promise<ApiResponse<{ results: ReturnRequest[]; count: number }>> {
    const q = new URLSearchParams();
    if (params?.status) q.append("status", params.status);
    if (params?.order) q.append("order", String(params.order));
    if (params?.page) q.append("page", String(params.page));
    return apiCall(`${ORDERS_API_URL}/returns/${q.toString() ? `?${q}` : ""}`);
  },

  async create(data: { item_id: number; kind: ReturnKind; quantity: number; reason: string }): Promise<ApiResponse<ReturnRequest>> {
    return apiCall(`${ORDERS_API_URL}/returns/`, { method: "POST", body: JSON.stringify(data) });
  },

  async setStatus(id: number, data: { status: ReturnStatus; resolution?: ReturnResolution; restock?: boolean; comment?: string }): Promise<ApiResponse<ReturnRequest>> {
    return apiCall(`${ORDERS_API_URL}/returns/${id}/status/`, { method: "POST", body: JSON.stringify(data) });
  },
};

export const reviewsApi = {
  async list(productId: number, page = 1): Promise<ApiResponse<ReviewsPage>> {
    return apiCall(`${API_URL}/products/${productId}/reviews/?page=${page}`);
  },

  async save(productId: number, data: { rating: number; text: string }): Promise<ApiResponse<Review>> {
    return apiCall(`${API_URL}/products/${productId}/reviews/`, { method: "POST", body: JSON.stringify(data) });
  },

  async setPublished(reviewId: number, isPublished: boolean): Promise<ApiResponse<Review>> {
    return apiCall(`${API_URL}/reviews/${reviewId}/`, { method: "PATCH", body: JSON.stringify({ is_published: isPublished }) });
  },
};

export const purchaseOrderApi = {
  async list(params?: { status?: PurchaseOrderStatus; page?: number }): Promise<ApiResponse<{ results: PurchaseOrder[]; count: number }>> {
    const q = new URLSearchParams();
    if (params?.status) q.append("status", params.status);
    if (params?.page) q.append("page", String(params.page));
    return apiCall(`${INVENTORY_API_URL}/purchase-orders/${q.toString() ? `?${q}` : ""}`);
  },

  async create(data: { supplier: number; warehouse: number; expected_date?: string | null; notes?: string; items: PurchaseOrderItem[] }): Promise<ApiResponse<PurchaseOrder>> {
    return apiCall(`${INVENTORY_API_URL}/purchase-orders/`, { method: "POST", body: JSON.stringify(data) });
  },

  async setStatus(id: number, status: "sent" | "cancelled"): Promise<ApiResponse<PurchaseOrder>> {
    return apiCall(`${INVENTORY_API_URL}/purchase-orders/${id}/status/`, { method: "POST", body: JSON.stringify({ status }) });
  },
};

export const promoCodeApi = {
  async list(): Promise<ApiResponse<{ results: PromoCode[]; count: number }>> {
    return apiCall(`${ORDERS_API_URL}/promo-codes/?page_size=100`);
  },

  async create(data: Partial<PromoCode>): Promise<ApiResponse<PromoCode>> {
    return apiCall(`${ORDERS_API_URL}/promo-codes/`, { method: "POST", body: JSON.stringify(data) });
  },

  async update(id: number, data: Partial<PromoCode>): Promise<ApiResponse<PromoCode>> {
    return apiCall(`${ORDERS_API_URL}/promo-codes/${id}/`, { method: "PATCH", body: JSON.stringify(data) });
  },
};

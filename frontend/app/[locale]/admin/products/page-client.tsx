"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Button } from "~/ui/primitives/button";
import {
  Package,
  Plus,
  Filter,
  RefreshCw,
  X,
} from "lucide-react";
import type { Product, Category } from "~/lib/types";
import { ConfirmDialog, StatsGridSkeleton, AdminPageHeader } from "../components";
import { useDebounce } from "~/lib/hooks/use-debounce";
import { useRecentProducts } from "~/lib/hooks/use-recent-products";
import { useCurrentUser } from "~/lib/auth-client";
import { useActivityFeed } from "../components/activity-feed";
import { ProductFormDialog } from "./product-form-dialog";
import { useProducts, useCategories, useDeleteProduct } from "~/lib/hooks/use-api-data";
import { ErrorAlert } from "~/ui/components/error-alert";
import { Pagination } from "~/ui/components/pagination";
import { ProductStatsCards } from "./product-stats";
import { useDraftFilters } from "~/lib/hooks/use-draft-filters";
import { ProductFilters } from "./product-filters";
import { ProductTable } from "./product-table";

const PAGE_SIZE = 20;

export function AdminProductsClient({
  initialProducts,
  initialTotalCount,
}: {
  initialProducts: Product[];
  initialTotalCount: number;
}) {
  const [currentPage, setCurrentPage] = useState(1);
  const [searchTerm, setSearchTerm] = useState("");
  const debouncedSearchTerm = useDebounce(searchTerm, 500);
  const [showFilters, setShowFilters] = useState(false);
  const filters = useDraftFilters({
    filterMinPrice: "",
    filterMaxPrice: "",
    filterMinStock: "",
    filterMaxStock: "",
    filterInStock: undefined as boolean | undefined,
    filterCategory: "",
    filterDateFrom: "",
    filterDateTo: "",
  });
  // The query uses what was applied; the panel edits filters.draft
  const {
    filterMinPrice, filterMaxPrice, filterMinStock, filterMaxStock,
    filterInStock, filterCategory, filterDateFrom, filterDateTo,
  } = filters.applied;
  const [sortField, setSortField] = useState("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [deleteDialog, setDeleteDialog] = useState<{
    open: boolean;
    productId: number | null;
    productName: string;
  }>({ open: false, productId: null, productName: "" });
  const [deleting, setDeleting] = useState(false);

  const [showFormDialog, setShowFormDialog] = useState(false);
  const [formDialogMode, setFormDialogMode] = useState<"create" | "edit">("create");
  const [formDialogProduct, setFormDialogProduct] = useState<Product | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const t = useTranslations("products");
  const tc = useTranslations("common");
  const tAct = useTranslations("activity");

  const { addProduct: addRecentProduct } = useRecentProducts();
  const { user } = useCurrentUser();
  const { pushEvent } = useActivityFeed();
  const isAdmin = user?.role === "admin";

  const params = {
    search: debouncedSearchTerm || undefined,
    page: currentPage,
    pageSize: PAGE_SIZE,
    minPrice: filterMinPrice ? parseFloat(filterMinPrice) : undefined,
    maxPrice: filterMaxPrice ? parseFloat(filterMaxPrice) : undefined,
    minStock: filterMinStock ? parseInt(filterMinStock, 10) : undefined,
    maxStock: filterMaxStock ? parseInt(filterMaxStock, 10) : undefined,
    inStock: filterInStock,
    category: filterCategory ? parseInt(filterCategory, 10) : undefined,
    createdAfter: filterDateFrom || undefined,
    createdBefore: filterDateTo || undefined,
    ordering: sortField ? (sortDir === "desc" ? `-${sortField}` : sortField) : undefined,
  } as Record<string, string | number | boolean | undefined>;

  const { data: pageData, error, isLoading, isValidating, mutate } = useProducts(params);
  const { data: catData, mutate: mutateCategories } = useCategories();
  const { trigger: deleteProductTrigger } = useDeleteProduct();

  const products = (pageData?.results || initialProducts).map((p) => ({
    ...p,
    price: Number(p.price),
    original_price: Number(p.original_price),
    rating: Number(p.rating),
  }));
  const totalCount = pageData?.count ?? initialTotalCount;
  const categories = (catData?.results || []) as Category[];
  const totalPages = Math.ceil(totalCount / PAGE_SIZE);

  const handleDeleteClick = (product: Product) => {
    setDeleteDialog({
      open: true,
      productId: product.id,
      productName: product.name,
    });
  };

  const handleDeleteConfirm = async () => {
    if (!deleteDialog.productId) return;
    setDeleting(true);
    try {
      await deleteProductTrigger(deleteDialog.productId);
      toast.success(t("deleteTitle"), {
        description: deleteDialog.productName,
      });
      pushEvent({ type: "delete", message: tAct("deletedProduct", { name: deleteDialog.productName }), entityType: "product" });
      setDeleteDialog({ open: false, productId: null, productName: "" });
      mutate();
    } catch (err) {
      toast.error(tc("error"), {
        description: err instanceof Error ? err.message : `${t("deleteTitle")} — ${tc("error")}`,
      });
    } finally {
      setDeleting(false);
    }
  };

  const handleAddClick = () => {
    setFormDialogMode("create");
    setFormDialogProduct(null);
    setShowFormDialog(true);
  };


  const handleEditClick = (product: Product) => {
    setFormDialogMode("edit");
    setFormDialogProduct(product);
    setShowFormDialog(true);
  };

  const handleSort = (field: string) => {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
    setCurrentPage(1);
  };

  const handleExport = () => {
    const headers = [t("id"), t("name"), t("category"), t("price"), t("origPrice"), t("stock"), t("inStock"), t("description")];
    const rows = products.map((p) => [
      p.id,
      `"${p.name.replace(/"/g, '""')}"`,
      `"${(p.category_name || "").replace(/"/g, '""')}"`,
      p.price,
      p.original_price || "",
      p.stock,
      p.in_stock ? tc("yes") : tc("no"),
      `"${(p.description || "").replace(/"/g, '""')}"`,
    ]);

    const csv = [headers.join(","), ...rows.map((r) => r.join(","))].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `products-export-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    toast.success(tc("export"), { description: `${products.length} ${tc("items").toLowerCase()}` });
  };

  const handleToggleExpand = (id: number) => {
    setExpandedId((prev) => {
      const next = prev === id ? null : id;
      if (next !== null) {
        const product = products.find((p) => p.id === id);
        if (product) {
          addRecentProduct({ id: product.id, name: product.name, price: Number(product.price), image_url: product.image_url });
        }
      }
      return next;
    });
  };

  const handleFilterChange = (key: string, value: string | boolean | undefined) => {
    filters.set(key as keyof typeof filters.draft, value as never);
  };

  const handleClearFilters = () => {
    filters.reset();
    setSortField("");
    setSortDir("desc");
    setCurrentPage(1);
  };

  const handleApplyFilters = () => {
    filters.apply();
    setCurrentPage(1);
  };

  const handleToggleFilters = () => {
    if (!showFilters) setCurrentPage(1);
    setShowFilters(!showFilters);
  };

  const stats = {
    total: products.length,
    active: products.filter((p) => p.in_stock).length,
    lowStock: products.filter((p) => p.stock < 10).length,
    totalValue: products.reduce((sum, p) => sum + p.price * p.stock, 0),
  };

  return (
    <div className="min-h-dvh bg-muted/50 p-4 sm:p-8">
      <div className="max-w-7xl mx-auto">
        <AdminPageHeader
          title={t("title")}
          subtitle={t("subtitle")}
          icon={Package}
          backLabel={tc("back")}
          actions={isAdmin ? (
            <Button className="flex items-center gap-2" onClick={handleAddClick}>
              <Plus className="h-4 w-4" />
              {t("addProduct")}
            </Button>
          ) : undefined}
        />

        <ErrorAlert message={error?.message || null} />

        <div className="mb-4 flex items-center justify-end">
          <Button variant="outline" size="sm" onClick={() => mutate()} disabled={isValidating} className="h-9">
            <RefreshCw className={`h-3.5 w-3.5 mr-1.5 ${isValidating ? "animate-spin" : ""}`} />
            {tc("refresh")}
          </Button>
        </div>

        {isLoading ? (
          <StatsGridSkeleton count={4} />
        ) : (
          <ProductStatsCards stats={stats} />
        )}

        <ProductTable
          products={products}
          onSort={handleSort}
          onEdit={handleEditClick}
          onDelete={handleDeleteClick}
          expandedId={expandedId}
          onToggleExpand={(id) => handleToggleExpand(id as number)}
          isAdmin={isAdmin}
          onExport={handleExport}
          sortField={sortField}
          sortDir={sortDir}
          searchTerm={searchTerm}
          isLoading={isLoading}
          isValidating={isValidating}
          filterToggle={
            <Button variant="outline" size="sm" onClick={handleToggleFilters}>
              {showFilters ? <X className="h-4 w-4 mr-2" /> : <Filter className="h-4 w-4 mr-2" />}
              {showFilters ? tc("close") : tc("filter")}
            </Button>
          }
        >
          <ProductFilters
            searchTerm={searchTerm}
            onSearchChange={setSearchTerm}
            showFilters={showFilters}
            filterMinPrice={filters.draft.filterMinPrice}
            filterMaxPrice={filters.draft.filterMaxPrice}
            filterMinStock={filters.draft.filterMinStock}
            filterMaxStock={filters.draft.filterMaxStock}
            filterInStock={filters.draft.filterInStock}
            filterCategory={filters.draft.filterCategory}
            filterDateFrom={filters.draft.filterDateFrom}
            filterDateTo={filters.draft.filterDateTo}
            categories={categories}
            onFilterChange={handleFilterChange}
            onClearFilters={handleClearFilters}
            onApplyFilters={handleApplyFilters}
          />
        </ProductTable>

        <Pagination
          currentPage={currentPage}
          totalPages={totalPages}
          loading={isLoading}
          onPageChange={(p) => { setCurrentPage(p); mutate(); }}
        />

        <ConfirmDialog
          open={deleteDialog.open}
          onOpenChange={(open) => setDeleteDialog({ ...deleteDialog, open })}
          onConfirm={handleDeleteConfirm}
          title={t("deleteTitle")}
          description={t("deleteConfirm", { name: deleteDialog.productName })}
          confirmText={tc("delete")}
          cancelText={tc("cancel")}
          variant="destructive"
          loading={deleting}
        />

        {/* Keyed per product: the form reads its fields once, when it mounts */}
        <ProductFormDialog
          key={`${formDialogMode}-${formDialogProduct?.id ?? "new"}`}
          open={showFormDialog}
          onOpenChange={setShowFormDialog}
          mode={formDialogMode}
          product={formDialogProduct}
          categories={categories}
          onSuccess={() => mutate()}
          onCategoryCreated={() => mutateCategories()}
        />
      </div>
    </div>
  );
}

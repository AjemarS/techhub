"use client";

import { useMemo } from "react";
import { useTranslations } from "next-intl";
import { Download, ChevronDown, Package, Edit, Trash2 } from "lucide-react";
import { Button } from "~/ui/primitives/button";
import { Badge } from "~/ui/primitives/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "~/ui/primitives/card";
import { DataTable, type Column } from "../components";
import { formatCurrency } from "~/lib/utils/format";
import type { Product } from "~/lib/types";

interface ProductTableProps {
  products: Product[];
  onSort: (field: string) => void;
  onEdit: (product: Product) => void;
  onDelete: (product: Product) => void;
  expandedId: number | null;
  onToggleExpand: (id: number) => void;
  isAdmin: boolean;
  onExport: () => void;
  sortField: string;
  sortDir: "asc" | "desc";
  searchTerm?: string;
  isLoading?: boolean;
  isValidating?: boolean;
  children?: React.ReactNode;
  filterToggle?: React.ReactNode;
}

export function ProductTable({
  products,
  onSort,
  onEdit,
  onDelete,
  expandedId,
  onToggleExpand,
  isAdmin,
  onExport,
  sortField,
  sortDir,
  searchTerm,
  isLoading,
  isValidating,
  children,
  filterToggle,
}: ProductTableProps) {
  const t = useTranslations("products");
  const tc = useTranslations("common");

  const columns: Column<Product>[] = useMemo(() => [
    {
      id: "expand",
      header: "",
      cell: (product) => (
        <ChevronDown className={`h-4 w-4 transition-transform ${expandedId === product.id ? "rotate-180" : ""}`} />
      ),
      className: "w-10",
    },
    {
      id: "id",
      header: t("id"),
      sortable: true,
      cell: (product) => <span className="font-medium">#{product.id}</span>,
    },
    {
      id: "name",
      header: t("name"),
      sortable: true,
      cell: (product) => <span className="font-medium">{product.name}</span>,
    },
    {
      id: "category",
      header: t("category"),
      cell: (product) => <span className="text-muted-foreground">{product.category_name || "N/A"}</span>,
    },
    {
      id: "price",
      header: t("price"),
      sortable: true,
      cell: (product) => <span className="font-semibold">{formatCurrency(product.price)}</span>,
    },
    {
      id: "stock",
      header: t("stock"),
      sortable: true,
      cell: (product) => (
        <Badge variant={product.stock < 10 ? "destructive" : product.stock < 50 ? "outline" : "default"}>
          {t("units", { count: product.stock })}
        </Badge>
      ),
    },
    {
      id: "status",
      header: tc("status"),
      cell: (product) => (
        <Badge variant={product.in_stock ? "default" : "secondary"}>
          {product.in_stock ? t("inStock") : t("outOfStock")}
        </Badge>
      ),
    },
    {
      id: "actions",
      header: tc("actions"),
      className: "text-right",
      headerClassName: "text-right",
      cell: (product) => (
        <div className="flex justify-end gap-2">
          {isAdmin && (
            <>
              <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); onEdit(product); }} title={tc("edit")}>
                <Edit className="h-4 w-4" />
              </Button>
              <Button size="sm" variant="destructive" onClick={(e) => { e.stopPropagation(); onDelete(product); }} title={tc("delete")}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </>
          )}
        </div>
      ),
    },
  ], [t, tc, expandedId, isAdmin, onEdit, onDelete]);

  return (
    <Card className="dark:bg-card dark:border-border">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-foreground">{t("catalog")}</CardTitle>
            <CardDescription className="text-muted-foreground">{t("catalogDesc")}</CardDescription>
          </div>
          <div className="flex gap-2">
            {filterToggle}
            <Button variant="outline" size="sm" onClick={onExport}>
              <Download className="h-4 w-4 mr-2" />
              {tc("export")}
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {children}
        <DataTable
          columns={columns}
          data={products}
          isLoading={isLoading}
          isValidating={isValidating}
          sortField={sortField}
          sortDir={sortDir}
          onSort={onSort}
          emptyMessage={searchTerm ? t("noResults") : t("noProducts")}
          emptyIcon={Package}
          keyExtractor={(p) => p.id}
          expandedId={expandedId}
          onToggleExpand={(id) => onToggleExpand(id as number)}
          renderExpandedContent={(product) => (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
              <div className="space-y-2">
                <div><span className="font-medium text-muted-foreground">{t("id")}:</span> <span>#{product.id}</span></div>
                <div><span className="font-medium text-muted-foreground">{t("category")}:</span> <span>{product.category_name || "N/A"}</span></div>
                <div><span className="font-medium text-muted-foreground">{t("price")}:</span> <span className="font-semibold">{formatCurrency(product.price)}</span></div>
                <div><span className="font-medium text-muted-foreground">{t("origPrice")}:</span> <span className="text-muted-foreground line-through">{formatCurrency(product.original_price)}</span></div>
              </div>
              <div className="space-y-2">
                <div><span className="font-medium text-muted-foreground">{t("stock")}:</span> <span>{t("units", { count: product.stock })}</span></div>
                <div><span className="font-medium text-muted-foreground">{t("rating")}:</span> <span>{Number(product.rating).toFixed(1)} / 5</span></div>
                <div><span className="font-medium text-muted-foreground">{tc("status")}:</span> <span>{product.in_stock ? t("inStock") : t("outOfStock")}</span></div>
                {product.image_url && (
                  <div><span className="font-medium text-muted-foreground">{t("image")}:</span> <span className="text-muted-foreground text-xs truncate block max-w-50">{product.image_url}</span></div>
                )}
              </div>
              <div className="space-y-2">
                <div className="font-medium text-muted-foreground">{t("description")}:</div>
                <p className="line-clamp-3">{product.description || tc("noData")}</p>
                {product.features && product.features.length > 0 && (
                  <>
                    <div className="font-medium text-muted-foreground">{t("features")}:</div>
                    <div className="flex flex-wrap gap-1">
                      {product.features.map((f, i) => (
                        <Badge key={`feature-${i}`} variant="outline" className="text-xs">{f}</Badge>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
        />
      </CardContent>
    </Card>
  );
}

import { Suspense } from "react";
import { notFound } from "next/navigation";
import ProductDetailClient from "./page-client"
import { ProductDetail } from "~/lib/types";
import { catalogFetch } from "~/lib/api/catalog-server";

async function getProduct(slug: string, lang: string): Promise<ProductDetail | null> {
  try {
    const response = await catalogFetch(`/products/${slug}/`, {
      headers: { "Accept-Language": lang },
      next: { revalidate: 30 },
    });
    if (!response.ok) return null;
    const data = await response.json();
    return {
      ...data,
      price: Number(data.price),
      original_price: Number(data.original_price),
      rating: Number(data.rating),
      features: data.features ?? [],
      specs: data.specs ?? {},
      image: data.image ?? "",
      // The API sends the category id plus its (localised) name
      category: { id: data.category, name: data.category_name ?? "", image: "", product_count: 0 },
    } as ProductDetail;
  } catch {
    return null;
  }
}

export default async function ProductPage({ params }: { params: Promise<{ slug: string; locale: string }> }) {
  const { slug, locale } = await params;
  const product = await getProduct(slug, locale === "en" ? "en" : "uk");
  if (!product) notFound();

  return (
    <Suspense fallback={null}>
      <ProductDetailClient product={product} />
    </Suspense>
  );
}

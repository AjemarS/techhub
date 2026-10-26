import { getLocale, getTranslations } from "next-intl/server";
import { ArrowRight, CreditCard, RotateCcw, Store, Tag } from "lucide-react";
import { Link } from "~/i18n/navigation";

import { CategoryImage } from "~/ui/components/category-image";
import { NewArrivalsCartWrapper } from "~/ui/components/new-arrivals-cart-wrapper";

import { Category, Product } from "../../lib/types";
import { catalogFetch } from "~/lib/api/catalog-server";

const API_URL = process.env.API_SERVICE_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost/api";
// shortcut: the advertised code is fixed here; move it to a setting once admins pick featured codes
const HOME_PROMO = "WELCOME10";

// null = the catalog could not be reached (shown to the visitor), [] = it is simply empty
async function getList<T>(path: string, lang: string): Promise<T[] | null> {
  try {
    const response = await catalogFetch(path, { headers: { "Accept-Language": lang }, next: { revalidate: 60 } });
    if (!response.ok) return null;
    const data = await response.json();
    return data.results || data;
  } catch {
    return null;
  }
}

/** The banner's offer, straight from the order service: no banner when the code is off or unreachable */
async function getPromo(): Promise<{ code: string; kind: string; value: string } | null> {
  try {
    // A large subtotal so a minimum order amount does not hide the offer
    const response = await fetch(`${API_URL}/orders/promo-check/?code=${HOME_PROMO}&subtotal=1000000`, {
      next: { revalidate: 300 },
    });
    return response.ok ? response.json() : null;
  } catch {
    return null;
  }
}

/** Title on the left, a "shop" text link on the right */
function SectionHeading({ title, linkText, href }: { title: string; linkText: string; href: string }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
      <h2 className="text-2xl font-bold tracking-tight md:text-3xl">{title}</h2>
      <Link href={href} className="group inline-flex items-center gap-1 font-medium text-primary hover:underline underline-offset-4">
        {linkText}
        <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" aria-hidden />
      </Link>
    </div>
  );
}

export default async function HomePage() {
  const lang = (await getLocale()) === "en" ? "en" : "uk";
  const [loadedCategories, loadedProducts, loadedSale, promo] = await Promise.all([
    getList<Category>("/categories/", lang),
    getList<Product>("/products/?ordering=-created_at&page_size=8", lang),
    getList<Product>("/products/?on_sale=true&in_stock=true&ordering=-created_at&page_size=8", lang),
    getPromo(),
  ]);
  const catalogDown = loadedCategories === null || loadedProducts === null;
  // Subcategories are reached from their parent, not shown next to it
  const categories = (loadedCategories ?? []).filter((c) => !c.parent);
  const latestProducts = loadedProducts ?? [];
  const saleProducts = loadedSale ?? [];
  const t = await getTranslations("home");
  const tErr = await getTranslations("errors");

  const trust = [
    { icon: Store, text: t("trustPickup") },
    { icon: CreditCard, text: t("trustCard") },
    { icon: RotateCcw, text: t("trustReturns") },
  ];

  return (
    <div className="container mx-auto max-w-7xl space-y-10 px-4 py-6 sm:px-6 md:py-8 lg:px-8">
      {/* The page's title for screen readers and search; sighted visitors start at the catalog */}
      <h1 className="sr-only">{t("heroTitle")} {t("heroTitleHighlight")}</h1>

      {catalogDown && (
        <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-center text-sm text-destructive">
          {tErr("catalogDown")}
        </p>
      )}

      {/* Categories first: one tap into the right part of the catalog */}
      {categories.length > 0 && (
        <nav aria-label={t("categoriesTitle")}>
          <ul className="-mx-4 flex snap-x scroll-px-4 gap-3 overflow-x-auto px-4 pb-2 sm:mx-0 sm:grid sm:grid-cols-4 sm:overflow-visible sm:px-0 lg:grid-cols-8">
            {categories.map((category) => (
              <li key={category.id} className="w-28 shrink-0 snap-start sm:w-auto">
                <Link
                  href={`/products?category=${category.id}`}
                  className="group flex h-full flex-col overflow-hidden rounded-lg border bg-card transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="relative aspect-4/3 overflow-hidden bg-muted">
                    <CategoryImage
                      categoryId={category.id ?? 0}
                      categoryName={category.name}
                      className="transition-transform duration-300 group-hover:scale-105 motion-reduce:transition-none"
                      fill
                      imageUrl={category.image_url}
                      sizes="(max-width: 640px) 112px, (max-width: 1024px) 25vw, 12vw"
                    />
                  </div>
                  <span className="px-2 py-2 text-center text-sm leading-tight font-medium">{category.name}</span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      )}

      {promo && (
        <Link
          href="/products"
          className="flex items-center gap-3 rounded-lg border border-primary/30 bg-primary/10 px-4 py-3 text-sm transition-colors hover:bg-primary/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Tag className="h-5 w-5 shrink-0 text-primary" aria-hidden />
          <span className="flex-1">
            {t("promoBanner", {
              discount: promo.kind === "percent" ? `${Number(promo.value)} %` : `${Number(promo.value)} ₴`,
              code: promo.code,
            })}
          </span>
          <ArrowRight className="h-4 w-4 shrink-0 text-primary" aria-hidden />
        </Link>
      )}

      {latestProducts.length > 0 && (
        <section>
          <SectionHeading title={t("newArrivalsTitle")} linkText={t("shopLink")} href="/products" />
          <NewArrivalsCartWrapper products={latestProducts} />
        </section>
      )}

      {saleProducts.length > 0 && (
        <section>
          <SectionHeading title={t("saleTitle")} linkText={t("shopLink")} href="/products?sale=1" />
          <NewArrivalsCartWrapper products={saleProducts} />
        </section>
      )}

      <ul className="flex flex-wrap justify-center gap-x-8 gap-y-3 border-t pt-6 text-sm text-muted-foreground">
        {trust.map(({ icon: Icon, text }) => (
          <li key={text} className="flex items-center gap-2">
            <Icon className="h-4 w-4 text-primary" aria-hidden />
            {text}
          </li>
        ))}
      </ul>
    </div>
  );
}

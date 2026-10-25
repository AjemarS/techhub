"use client";

import { formatCurrency } from "~/lib/utils/format";
import { apiErrorText } from "~/lib/utils/error-handler";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "~/ui/primitives/dialog";
import { Button } from "~/ui/primitives/button";
import { Input } from "~/ui/primitives/input";
import { Label } from "~/ui/primitives/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "~/ui/primitives/select";
import { Textarea } from "~/ui/primitives/textarea";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "~/ui/primitives/collapsible";
import { Alert, AlertDescription } from "~/ui/primitives/alert";
import { AlertCircle, ChevronDown, ChevronRight, Plus, X, Upload } from "lucide-react";
import { productApi, categoryApi } from "~/lib/api/admin-api";
import { API_URL } from "~/lib/api/client";
import type { Product, Category } from "~/lib/types";
import { useActivityFeed } from "../components/activity-feed";

interface SpecRow {
  key: string;
  value: string;
}

interface ProductFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: "create" | "edit";
  product: Product | null;
  categories: Category[];
  onSuccess: () => void;
  onCategoryCreated: (cat: Category) => void;
}

function FeaturesInput({ features, onChange }: { features: string[]; onChange: (f: string[]) => void }) {
  const tf = useTranslations("productForm");
  const [input, setInput] = useState("");

  const addFeature = () => {
    const val = input.trim();
    if (val && !features.includes(val)) {
      onChange([...features, val]);
    }
    setInput("");
  };

  return (
    <div className="col-span-3">
      <div className="flex flex-wrap gap-1.5 mb-2">
        {features.map((f, i) => (
          <span key={`feature-${f}-${i}`} className="inline-flex items-center gap-1 rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
            {f}
            <button type="button" onClick={() => onChange(features.filter((_, j) => j !== i))} className="hover:text-destructive" aria-label={tf("removeFeature", { name: f })}>
              <X className="h-3 w-3" aria-hidden />
            </button>
          </span>
        ))}
      </div>
      <Input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addFeature(); } }}
        placeholder={tf("featurePlaceholder")}
      />
    </div>
  );
}

function SpecsEditor({ specs, onChange }: { specs: SpecRow[]; onChange: (s: SpecRow[]) => void }) {
  const tf = useTranslations("productForm");
  const addRow = () => onChange([...specs, { key: "", value: "" }]);
  const removeRow = (i: number) => onChange(specs.filter((_, j) => j !== i));
  const updateRow = (i: number, field: "key" | "value", val: string) =>
    onChange(specs.map((s, j) => (j === i ? { ...s, [field]: val } : s)));

  return (
    <div className="col-span-3 space-y-2">
      {specs.map((spec, i) => (
        <div key={`spec-${i}`} className="flex gap-2">
          <Input placeholder={tf("specKey")} aria-label={tf("specKey")} value={spec.key} onChange={(e) => updateRow(i, "key", e.target.value)} className="flex-1" />
          <Input placeholder={tf("specValue")} aria-label={tf("specValue")} value={spec.value} onChange={(e) => updateRow(i, "value", e.target.value)} className="flex-1" />
          <Button variant="ghost" size="icon" onClick={() => removeRow(i)} className="shrink-0" type="button" aria-label={tf("removeSpec")}>
            <X className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={addRow} className="w-full" type="button">
        <Plus className="h-4 w-4 mr-2" aria-hidden /> {tf("addSpec")}
      </Button>
    </div>
  );
}

function CategoryCreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onCreated: (cat: Category) => void;
}) {
  const t = useTranslations("categories");
  const tc = useTranslations("common");
  const tf = useTranslations("productForm");
  const [name, setName] = useState("");
  const [nameUk, setNameUk] = useState("");
  const [nameEn, setNameEn] = useState("");
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    try {
      const res = await categoryApi.create({
        name: name.trim(),
        name_uk: nameUk.trim() || undefined,
        name_en: nameEn.trim() || undefined,
      });
      if (res.error) {
        toast.error(tf("categoryCreateFailed"), { description: res.error.message });
      } else if (res.data) {
        onCreated(res.data);
        onOpenChange(false);
        setName("");
        setNameUk("");
        setNameEn("");
        toast.success(t("createdSuccess"), { description: t("createdDesc", { name: name.trim() }) });
      }
    } catch (err) {
      toast.error(tc("error"), { description: err instanceof Error ? err.message : tc("error") });
    } finally {
      setCreating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onOpenChange(false); }}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{t("addCategory")}</DialogTitle>
          <DialogDescription>{t("createDialogDesc")}</DialogDescription>
        </DialogHeader>
        <div className="py-4 space-y-3">
          <div>
            <Label htmlFor="cat-name">{t("namePrimary")}</Label>
            <Input id="cat-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={t("namePrimaryPlaceholder")} className="mt-2" autoFocus />
          </div>
          <div>
            <Label htmlFor="cat-name-uk">{t("nameUa")}</Label>
            <Input id="cat-name-uk" value={nameUk} onChange={(e) => setNameUk(e.target.value)} placeholder={t("nameUkPlaceholder")} className="mt-2" />
          </div>
          <div>
            <Label htmlFor="cat-name-en">{t("nameEn")}</Label>
            <Input id="cat-name-en" value={nameEn} onChange={(e) => setNameEn(e.target.value)} placeholder={t("nameEnPlaceholder")} className="mt-2" />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={creating} type="button">{tc("cancel")}</Button>
          <Button onClick={handleCreate} disabled={creating || !name.trim()} type="button">
            {creating ? t("creatingButton") : t("createButton")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FormSection({ title, defaultOpen = false, children }: { title: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="border border-border rounded-lg p-4">
      <CollapsibleTrigger className="flex w-full items-center justify-between text-sm font-medium cursor-pointer">
        <span className="text-foreground">{title}</span>
        {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-4">
        <div className="space-y-4">
          {children}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function ProductFormDialog({
  open,
  onOpenChange,
  mode,
  product,
  categories,
  onSuccess,
  onCategoryCreated,
}: ProductFormDialogProps) {
  const tf = useTranslations("productForm");
  const tc = useTranslations("common");
  const [name, setName] = useState(mode === "edit" ? product?.name ?? "" : "");
  const [nameUk, setNameUk] = useState(mode === "edit" ? product?.name_uk ?? "" : "");
  const [nameEn, setNameEn] = useState(mode === "edit" ? product?.name_en ?? "" : "");
  const [description, setDescription] = useState(mode === "edit" ? product?.description ?? "" : "");
  const [descriptionUk, setDescriptionUk] = useState(mode === "edit" ? product?.description_uk ?? "" : "");
  const [descriptionEn, setDescriptionEn] = useState(mode === "edit" ? product?.description_en ?? "" : "");
  const [categoryId, setCategoryId] = useState(() => {
    if (mode === "edit" && product) {
      const cid = typeof product.category === "object" ? product.category?.id : product.category;
      return String(cid ?? "");
    }
    return "";
  });
  const [price, setPrice] = useState(mode === "edit" ? String(product?.price ?? "") : "");
  const [originalPrice, setOriginalPrice] = useState(mode === "edit" ? String(product?.original_price ?? "") : "");
  const [warrantyMonths, setWarrantyMonths] = useState(mode === "edit" ? String(product?.warranty_months ?? 12) : "12");
  const [features, setFeatures] = useState<string[]>(mode === "edit" ? (product?.features ?? []) : []);
  const [specs, setSpecs] = useState<SpecRow[]>(() => {
    if (mode === "edit" && product?.specs && typeof product.specs === "object") {
      return Object.entries(product.specs).map(([key, value]) => ({ key, value: String(value) }));
    }
    return [];
  });
  const [image, setImage] = useState<File | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [showCategoryDialog, setShowCategoryDialog] = useState(false);
  const { pushEvent } = useActivityFeed();

  const resetForm = () => {
    if (mode === "create") {
      setName("");
      setNameUk("");
      setNameEn("");
      setDescription("");
      setDescriptionUk("");
      setDescriptionEn("");
      setCategoryId("");
      setPrice("");
      setOriginalPrice("");
      setWarrantyMonths("12");
      setFeatures([]);
      setSpecs([]);
      setImage(null);
    }
    setFormError(null);
  };

  const handleOpenChange = (open: boolean) => {
    if (!open) resetForm();
    onOpenChange(open);
  };

  const validate = (): string | null => {
    if (!name.trim()) return tf("errName");
    if (!description.trim()) return tf("errDescription");
    if (!categoryId) return tf("errCategory");
    const priceNum = parseFloat(price);
    if (!price || isNaN(priceNum) || priceNum <= 0) return tf("errPrice");
    const origNum = parseFloat(originalPrice);
    if (!originalPrice || isNaN(origNum) || origNum < 0) return tf("errOriginalPrice");
    if (origNum < priceNum) return tf("errOriginalBelowPrice");
    const warranty = Number(warrantyMonths);
    if (!Number.isInteger(warranty) || warranty < 0 || warranty > 120) return tf("errWarranty");
    return null;
  };

  const buildPayload = () => ({
    name: name.trim(),
    name_uk: nameUk.trim() || undefined,
    name_en: nameEn.trim() || undefined,
    description: description.trim(),
    description_uk: descriptionUk.trim() || undefined,
    description_en: descriptionEn.trim() || undefined,
    category: parseInt(categoryId, 10),
    price: parseFloat(price),
    original_price: parseFloat(originalPrice),
    warranty_months: parseInt(warrantyMonths || "0", 10),
    features,
    specs: specs.reduce<Record<string, string>>((acc, s) => {
      if (s.key.trim()) acc[s.key.trim()] = s.value;
      return acc;
    }, {}),
  });

  const handleSave = async () => {
    const error = validate();
    if (error) { setFormError(error); return; }
    setFormError(null);
    setSaving(true);

    try {
      const payload = buildPayload();
      let response;

      if (image) {
        const formData = new FormData();
        Object.entries(payload).forEach(([key, val]) => {
          formData.append(key, typeof val === "object" ? JSON.stringify(val) : String(val));
        });
        formData.append("image", image);

        const url = mode === "create"
          ? `${API_URL}/products/`
          : `${API_URL}/products/${product!.id}/`;

        const fetchRes = await fetch(url, {
          method: mode === "create" ? "POST" : "PUT",
          credentials: "include",
          body: formData,
        });

        if (!fetchRes.ok) {
          const errorData = await fetchRes.json().catch(() => ({ message: "Request failed" }));
          response = {
            error: { message: errorData.message || errorData.detail || "Request failed", status: fetchRes.status, details: errorData },
          };
        } else {
          const data = await fetchRes.json();
          response = { data };
        }
      } else {
        response = mode === "create"
          ? await productApi.create(payload)
          : await productApi.update(product!.id, payload);
      }

      if (response.error) {
        toast.error(mode === "create" ? tf("createFailed") : tf("updateFailed"), {
          description: apiErrorText(response.error),
        });
      } else {
        toast.success(mode === "create" ? tf("created", { name: name.trim() }) : tf("updated", { name: name.trim() }));
        pushEvent({ type: mode === "create" ? "create" : "update", message: tf(mode === "create" ? "eventCreated" : "eventUpdated", { name: name.trim() }), entityType: "product" });
        handleOpenChange(false);
        onSuccess();
      }
    } catch (err) {
      toast.error(tc("error"), {
        description: err instanceof Error ? err.message : tc("error"),
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{mode === "create" ? tf("addTitle") : tf("editTitle")}</DialogTitle>
            <DialogDescription>{mode === "create" ? tf("addDesc") : tf("editDesc")}</DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-4 max-h-[60vh] overflow-y-auto pr-2">
            <FormSection title={tf("sectionNames")} defaultOpen={true}>
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="pd-name" className="text-right">{tf("name")} *</Label>
                <Input id="pd-name" value={name} onChange={(e) => setName(e.target.value)} className="col-span-3" />
              </div>
              <div className="grid grid-cols-4 items-center gap-2">
                <Label htmlFor="pd-name-uk" className="text-right text-xs text-muted-foreground">UK</Label>
                <Input id="pd-name-uk" value={nameUk} onChange={(e) => setNameUk(e.target.value)} placeholder={tf("nameUkPlaceholder")} className="col-span-3" />
              </div>
              <div className="grid grid-cols-4 items-center gap-2">
                <Label htmlFor="pd-name-en" className="text-right text-xs text-muted-foreground">EN</Label>
                <Input id="pd-name-en" value={nameEn} onChange={(e) => setNameEn(e.target.value)} placeholder={tf("nameEnPlaceholder")} className="col-span-3" />
              </div>
            </FormSection>

            <FormSection title={tf("sectionDescriptions")}>
              <div className="grid grid-cols-4 items-start gap-4">
                <Label htmlFor="pd-description" className="text-right pt-2">{tf("description")} *</Label>
                <Textarea id="pd-description" value={description} onChange={(e) => setDescription(e.target.value)} className="col-span-3 min-h-20" />
              </div>
              <div className="grid grid-cols-4 items-start gap-2">
                <Label htmlFor="pd-description-uk" className="text-right text-xs text-muted-foreground pt-1">UK</Label>
                <Textarea id="pd-description-uk" value={descriptionUk} onChange={(e) => setDescriptionUk(e.target.value)} placeholder={tf("descriptionUkPlaceholder")} className="col-span-3 min-h-15" />
              </div>
              <div className="grid grid-cols-4 items-start gap-2">
                <Label htmlFor="pd-description-en" className="text-right text-xs text-muted-foreground pt-1">EN</Label>
                <Textarea id="pd-description-en" value={descriptionEn} onChange={(e) => setDescriptionEn(e.target.value)} placeholder={tf("descriptionEnPlaceholder")} className="col-span-3 min-h-15" />
              </div>
            </FormSection>

            <FormSection title={tf("sectionPricing")}>
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="pd-category" className="text-right">{tf("category")} *</Label>
                <div className="col-span-3 flex gap-2">
                  <Select value={categoryId} onValueChange={setCategoryId}>
                    <SelectTrigger id="pd-category" className="flex-1"><SelectValue placeholder={tf("selectCategory")} /></SelectTrigger>
                    <SelectContent>
                      {categories.map((cat) => (
                        <SelectItem key={cat.id} value={String(cat.id)}>{cat.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button variant="outline" size="sm" onClick={() => setShowCategoryDialog(true)} type="button" className="shrink-0">
                    <Plus className="h-4 w-4 mr-1" aria-hidden /> {tf("newCategory")}
                  </Button>
                </div>
              </div>

              <div className="grid grid-cols-4 items-start gap-4">
                <Label htmlFor="pd-price" className="flex min-h-9 items-center justify-end text-right leading-tight">{tf("price")} *</Label>
                <div className="col-span-3">
                  <Input id="pd-price" type="number" step="0.01" min="0" value={price} onChange={(e) => setPrice(e.target.value)}
                    onBlur={() => {
                      const num = parseFloat(price);
                      if (!isNaN(num)) setPrice(num.toFixed(2));
                    }}
                  />
                  {price && !isNaN(parseFloat(price)) && (
                    <p className="text-xs text-muted-foreground mt-1">
                      {formatCurrency(parseFloat(price))}
                    </p>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-4 items-start gap-4">
                <Label htmlFor="pd-original-price" className="flex min-h-9 items-center justify-end text-right leading-tight">{tf("originalPrice")} *</Label>
                <div className="col-span-3">
                  <Input id="pd-original-price" type="number" step="0.01" min="0" value={originalPrice} onChange={(e) => setOriginalPrice(e.target.value)}
                    onBlur={() => {
                      const num = parseFloat(originalPrice);
                      if (!isNaN(num)) setOriginalPrice(num.toFixed(2));
                    }}
                  />
                  {originalPrice && !isNaN(parseFloat(originalPrice)) && (
                    <p className="text-xs text-muted-foreground mt-1">
                      {formatCurrency(parseFloat(originalPrice))}
                    </p>
                  )}
                </div>
              </div>

            </FormSection>

            <FormSection title={tf("sectionMetadata")}>
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="pd-warranty" className="text-right">{tf("warranty")}</Label>
                <Input id="pd-warranty" type="number" step="1" min="0" max="120" value={warrantyMonths} onChange={(e) => setWarrantyMonths(e.target.value)} className="col-span-3" />
              </div>

              <div className="grid grid-cols-4 items-start gap-4">
                <Label className="text-right pt-1">{tf("features")}</Label>
                <FeaturesInput features={features} onChange={setFeatures} />
              </div>

              <div className="grid grid-cols-4 items-start gap-4">
                <Label className="text-right pt-1">{tf("specs")}</Label>
                <SpecsEditor specs={specs} onChange={setSpecs} />
              </div>
            </FormSection>

            <FormSection title={tf("sectionMedia")}>
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="pd-image" className="text-right">{tf("image")}</Label>
                <div className="col-span-3 flex items-center gap-3">
                  <Button variant="outline" size="sm" onClick={() => document.getElementById("pd-image")?.click()} type="button">
                    <Upload className="h-4 w-4 mr-1" aria-hidden /> {image ? tf("changeImage") : tf("uploadImage")}
                  </Button>
                  {image && <span className="text-sm text-muted-foreground truncate max-w-50">{image.name}</span>}
                  {imageError && <span role="alert" className="text-sm text-destructive">{imageError}</span>}
                  <input
                    id="pd-image"
                    type="file"
                    accept="image/jpeg,image/png,image/webp,image/gif"
                    className="hidden"
                    onChange={(e) => {
                      const file = e.target.files?.[0] ?? null;
                      setImageError(null);
                      if (file) {
                        const allowed = ["image/jpeg", "image/png", "image/webp", "image/gif"];
                        if (!allowed.includes(file.type)) {
                          setImageError(tf("errImageType"));
                          setImage(null);
                          return;
                        }
                        if (file.size > 5 * 1024 * 1024) {
                          setImageError(tf("errImageSize"));
                          setImage(null);
                          return;
                        }
                      }
                      setImage(file);
                    }}
                  />
                </div>
              </div>
            </FormSection>
          </div>

          {formError && (
            <Alert variant="destructive" className="mx-6 mb-2">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => handleOpenChange(false)} disabled={saving} type="button">{tc("cancel")}</Button>
            <Button onClick={handleSave} disabled={saving || !name.trim() || !description.trim() || !categoryId || !price || parseFloat(price) <= 0 || !originalPrice} type="button">
              {saving ? tf("saving") : mode === "create" ? tf("create") : tf("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CategoryCreateDialog
        open={showCategoryDialog}
        onOpenChange={setShowCategoryDialog}
        onCreated={(cat) => {
          onCategoryCreated(cat);
          setCategoryId(String(cat.id));
        }}
      />
    </>
  );
}

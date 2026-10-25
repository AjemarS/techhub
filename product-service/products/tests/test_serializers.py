
from django.test import TestCase

from ..serializers import CategorySerializer, ProductSerializer
from .helpers import _create_category, _create_product, _create_sample_image


class CategorySerializerTest(TestCase):
    def test_valid_data(self):
        serializer = CategorySerializer(data={"name": "Valid Category"})
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)

    def test_blank_name(self):
        serializer = CategorySerializer(data={"name": ""})
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)

    def test_whitespace_name(self):
        serializer = CategorySerializer(data={"name": "   "})
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)

    def test_product_count_field(self):
        cat = _create_category()
        _create_product(category=cat)
        serializer = CategorySerializer(cat)
        self.assertEqual(serializer.data["product_count"], 1)

    def test_name_max_length(self):
        serializer = CategorySerializer(data={"name": "x" * 101})
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)


class ProductSerializerTest(TestCase):
    def setUp(self):
        self.cat = _create_category()
        self.valid_payload = {
            "name": "Test Product",
            "description": "A great product",
            "category": self.cat.pk,
            "price": "49.99",
            "original_price": "79.99",
            "stock": 10,
            "features": ["feature1", "feature2"],
            "specs": {"color": "red", "weight": "1kg"},
            "rating": "4.5",
        }

    def test_valid_data(self):
        serializer = ProductSerializer(data=self.valid_payload)
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)

    def test_blank_name(self):
        payload = {**self.valid_payload, "name": ""}
        serializer = ProductSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("name", serializer.errors)

    def test_blank_description(self):
        payload = {**self.valid_payload, "description": ""}
        serializer = ProductSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("description", serializer.errors)

    def test_stock_cannot_be_set_directly(self):
        payload = {**self.valid_payload, "stock": 500}
        serializer = ProductSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("stock", serializer.validated_data)

    def test_rating_cannot_be_set_directly(self):
        # It is the average of reviews: a client-sent value is ignored
        serializer = ProductSerializer(data={**self.valid_payload, "rating": "5.0"})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("rating", serializer.validated_data)

    def test_features_must_be_list(self):
        payload = {**self.valid_payload, "features": "not-a-list"}
        serializer = ProductSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("features", serializer.errors)

    def test_features_items_must_be_strings(self):
        payload = {**self.valid_payload, "features": [1, 2, 3]}
        serializer = ProductSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("features", serializer.errors)

    def test_specs_must_be_dict(self):
        payload = {**self.valid_payload, "specs": ["not-a-dict"]}
        serializer = ProductSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("specs", serializer.errors)

    def test_original_price_less_than_current(self):
        payload = {**self.valid_payload, "original_price": "10.00", "price": "50.00"}
        serializer = ProductSerializer(data=payload)
        self.assertFalse(serializer.is_valid())
        self.assertIn("original_price", serializer.errors)

    def test_original_price_equal_to_current(self):
        payload = {**self.valid_payload, "original_price": "50.00", "price": "50.00"}
        serializer = ProductSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)

    def test_category_name_read_only(self):
        product = _create_product(category=self.cat)
        serializer = ProductSerializer(product)
        self.assertEqual(serializer.data["category_name"], self.cat.name)

    def test_in_stock_read_only(self):
        payload = {**self.valid_payload, "in_stock": False}
        serializer = ProductSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), msg=serializer.errors)
        self.assertNotIn("in_stock", serializer.validated_data)

    def test_valid_image_type(self):
        image = _create_sample_image()
        inst = ProductSerializer()
        result = inst.validate_image(image)
        self.assertEqual(result, image)


class SpecsEnglishTest(TestCase):
    def test_english_request_gets_translated_specs(self):
        from products.spec_translations import specs_to_en

        specs = {"Об'єм": "385 л", "Колір": "Білий", "Невідоме": "x"}
        product = _create_product(specs=specs, specs_en=specs_to_en(specs))
        en = self.client.get(f"/api/products/{product.pk}/", HTTP_ACCEPT_LANGUAGE="en").json()
        uk = self.client.get(f"/api/products/{product.pk}/", HTTP_ACCEPT_LANGUAGE="uk").json()
        self.assertEqual(en["specs"], {"Capacity": "385 L", "Colour": "White", "Невідоме": "x"})
        self.assertEqual(uk["specs"], specs)

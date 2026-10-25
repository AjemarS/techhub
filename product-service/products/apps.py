from django.apps import AppConfig


class ProductsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'products'

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from . import catalog_cache
        from .models import Category, Product

        # Admin edits go through save/delete; update() paths call catalog_cache.bump() themselves
        for model in (Product, Category):
            post_save.connect(lambda **kw: catalog_cache.bump(), sender=model, weak=False)
            post_delete.connect(lambda **kw: catalog_cache.bump(), sender=model, weak=False)

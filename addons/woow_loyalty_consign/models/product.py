from odoo import models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _load_pos_data(self, data):
        res = super()._load_pos_data(data)
        config_id = self.env['pos.config'].browse(
            data['pos.config']['data'][0]['id']
        )
        redemption_product = self.env.ref(
            'woow_loyalty_consign.consign_redemption_product',
            raise_if_not_found=False,
        )
        if not redemption_product:
            return res

        loaded_ids = {p['id'] for p in res['data']}
        if redemption_product.id not in loaded_ids:
            products = redemption_product.read(
                fields=res['fields'], load=False
            )
            self._process_pos_ui_product_product(products, config_id)
            res['data'].extend(products)
        return res

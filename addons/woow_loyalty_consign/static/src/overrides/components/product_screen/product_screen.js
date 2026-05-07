/** @odoo-module */

import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { ConsignCardPopup } from "@woow_loyalty_consign/overrides/components/consign_card_popup/consign_card_popup";

patch(ProductScreen.prototype, {
    setup() {
        super.setup(...arguments);
        this.consignNotification = useService("notification");
        this.consignDialog = useService("dialog");
    },
    /**
     * Override coupon scan: try consign card first, fall back to original behavior.
     * Depends on pos_loyalty having patched _onCouponScan first (guaranteed by
     * module dependency order in __manifest__.py).
     */
    async _onCouponScan(code) {
        const order = this.pos.get_order();
        const partnerId = order.get_partner()?.id || false;

        let result;
        try {
            result = await this.pos.data.call(
                "pos.config",
                "use_consign_card_code",
                [[this.pos.config.id], code.base_code, partnerId]
            );
        } catch (error) {
            console.error("[ConsignCard] Lookup RPC failed, falling back:", error);
            return super._onCouponScan(code);
        }

        if (result && result.successful) {
            this._openConsignCardPopup(result.payload);
        } else if (result && !result.successful && result.payload?.not_found) {
            return super._onCouponScan(code);
        } else if (result && !result.successful) {
            this.consignNotification.add(
                result.payload?.error_message || "寄品卡驗證失敗",
                { type: "danger" }
            );
        } else {
            return super._onCouponScan(code);
        }
    },

    _openConsignCardPopup(cardData) {
        this.consignDialog.add(ConsignCardPopup, {
            cardData,
            getPayload: async (consignSelection) => {
                await this._addConsignLinesToOrder(
                    consignSelection,
                    cardData.consign_redemption_product_id
                );
            },
        });
    },

    async _addConsignLinesToOrder(consignSelection, redemptionProductId) {
        const order = this.pos.get_order();

        if (!order.uiState.consignRedemptions) {
            order.uiState.consignRedemptions = [];
        }
        order.uiState.consignRedemptions.push(consignSelection);

        // Find the consign redemption product by ID (returned from backend RPC)
        const consignProduct = redemptionProductId
            ? this.pos.models["product.product"].get(redemptionProductId)
            : null;
        if (!consignProduct) {
            this.consignNotification.add("找不到寄品核銷商品，請確認已安裝模組。", {
                type: "danger",
            });
            return;
        }

        for (const line of consignSelection.lines) {
            await this.pos.addLineToCurrentOrder(
                {
                    product_id: consignProduct,
                    qty: line.qty_redeemed,
                    price_unit: 0,
                    customer_note: `[寄品] ${line.product_name}`,
                    consign_line_id: line.consign_line_id,
                    is_consign_redemption: true,
                },
                {},
                false
            );
        }

        this.consignNotification.add(
            `已加入 ${consignSelection.lines.length} 項寄品核銷品項`,
            { type: "success" }
        );
    },
});

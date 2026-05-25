/** @odoo-module */

import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { ConsignCardPopup } from "@woow_loyalty_consign_pos/overrides/components/consign_card_popup/consign_card_popup";
import { ConsignSearchPopup } from "@woow_loyalty_consign_pos/overrides/components/consign_search_popup/consign_search_popup";

/**
 * Patch ControlButtons to add the "Consignment" button handler.
 * The button is placed in the Actions popup (showRemainingButtons section).
 */
patch(ControlButtons.prototype, {
    onClickConsignRedemption() {
        this.dialog.add(ConsignSearchPopup, {
            pos: this.pos,
            onCardSelected: (cardData) => {
                this._openConsignCardPopup(cardData);
            },
        });
    },

    _openConsignCardPopup(cardData) {
        this.dialog.add(ConsignCardPopup, {
            cardData,
            getPayload: async (consignSelection) => {
                const order = this.pos.get_order();
                await this._addConsignLinesToOrder(
                    order,
                    consignSelection,
                    cardData.consign_redemption_product_id
                );
            },
        });
    },

    async _addConsignLinesToOrder(order, consignSelection, redemptionProductId) {
        if (!order.uiState.consignRedemptions) {
            order.uiState.consignRedemptions = [];
        }
        order.uiState.consignRedemptions.push(consignSelection);

        const consignProduct = redemptionProductId
            ? this.pos.models["product.product"].get(redemptionProductId)
            : null;
        if (!consignProduct) {
            this.notification.add(
                "Consignment redemption product not found. Please verify module installation.",
                { type: "danger" }
            );
            return;
        }

        for (const line of consignSelection.lines) {
            await this.pos.addLineToCurrentOrder(
                {
                    product_id: consignProduct,
                    qty: line.qty_redeemed,
                    price_unit: 0,
                    customer_note: `[Consign] ${line.product_name}`,
                    consign_line_id: line.consign_line_id,
                    is_consign_redemption: true,
                },
                {},
                false
            );
        }

        this.notification.add(
            `Added ${consignSelection.lines.length} consignment item(s) for pickup`,
            { type: "success" }
        );
    },
});

/**
 * Patch ProductScreen for barcode scan interception.
 * pos_loyalty already registers useBarcodeReader({coupon: this._onCouponScan})
 * and sets this.notification / this.dialog in setup, so we just override _onCouponScan.
 */
patch(ProductScreen.prototype, {
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
            this._openConsignCardPopupFromScan(result.payload);
        } else if (result && !result.successful && result.payload?.not_found) {
            return super._onCouponScan(code);
        } else if (result && !result.successful) {
            this.notification.add(
                result.payload?.error_message || "Card validation failed",
                { type: "danger" }
            );
        } else {
            return super._onCouponScan(code);
        }
    },

    _openConsignCardPopupFromScan(cardData) {
        this.dialog.add(ConsignCardPopup, {
            cardData,
            getPayload: async (consignSelection) => {
                const order = this.pos.get_order();
                if (!order.uiState.consignRedemptions) {
                    order.uiState.consignRedemptions = [];
                }
                order.uiState.consignRedemptions.push(consignSelection);

                const consignProduct = cardData.consign_redemption_product_id
                    ? this.pos.models["product.product"].get(cardData.consign_redemption_product_id)
                    : null;
                if (!consignProduct) {
                    this.notification.add(
                        "Consignment redemption product not found.",
                        { type: "danger" }
                    );
                    return;
                }

                for (const line of consignSelection.lines) {
                    await this.pos.addLineToCurrentOrder(
                        {
                            product_id: consignProduct,
                            qty: line.qty_redeemed,
                            price_unit: 0,
                            customer_note: `[Consign] ${line.product_name}`,
                            consign_line_id: line.consign_line_id,
                            is_consign_redemption: true,
                        },
                        {},
                        false
                    );
                }

                this.notification.add(
                    `Added ${consignSelection.lines.length} consignment item(s) for pickup`,
                    { type: "success" }
                );
            },
        });
    },
});

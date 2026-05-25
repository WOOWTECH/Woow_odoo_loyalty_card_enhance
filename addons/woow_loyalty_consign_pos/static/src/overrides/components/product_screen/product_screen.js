/** @odoo-module */

import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { ConsignCardPopup } from "@woow_loyalty_consign_pos/overrides/components/consign_card_popup/consign_card_popup";
import { ConsignCardListPopup } from "@woow_loyalty_consign_pos/overrides/components/consign_card_list_popup/consign_card_list_popup";

/**
 * Patch ControlButtons — "Consignment" button handler.
 * Customer-driven flow: auto-select partner → fetch cards → show list or direct popup.
 */
patch(ControlButtons.prototype, {
    async onClickConsignRedemption() {
        const order = this.pos.get_order();

        // Step 1: Ensure a customer is selected
        let partner = order.get_partner();
        if (!partner) {
            await this.pos.selectPartner();
            partner = order.get_partner();
            if (!partner) {
                // User cancelled partner selection
                return;
            }
        }

        // Step 2: Fetch this customer's consignment cards
        let result;
        try {
            result = await this.pos.data.call(
                "pos.config",
                "get_partner_consign_cards",
                [[this.pos.config.id], partner.id]
            );
        } catch (error) {
            console.error("[ConsignCard] get_partner_consign_cards RPC failed:", error);
            this.notification.add("Failed to load consignment cards.", { type: "danger" });
            return;
        }

        if (!result || !result.successful) {
            this.notification.add(
                result?.payload?.error_message || "Failed to load consignment cards.",
                { type: "danger" }
            );
            return;
        }

        const cards = result.payload.cards || [];

        // Step 3: Route based on card count
        if (cards.length === 0) {
            this.notification.add(
                `${partner.name} has no consignment cards available for redemption.`,
                { type: "warning" }
            );
            return;
        }

        if (cards.length === 1) {
            // Single card — go directly to item selection
            this._openConsignCardPopup(cards[0]);
            return;
        }

        // Multiple cards — show card list popup
        this.dialog.add(ConsignCardListPopup, {
            partnerName: partner.name,
            cards: cards,
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
 * On scan: validate card owner matches order customer, or auto-set customer.
 */
patch(ProductScreen.prototype, {
    async _onCouponScan(code) {
        const order = this.pos.get_order();
        const partner = order.get_partner();
        const partnerId = partner?.id || false;

        let result;
        try {
            result = await this.pos.data.call(
                "pos.config",
                "use_consign_card_code",
                [[this.pos.config.id], code.base_code, false]
            );
        } catch (error) {
            console.error("[ConsignCard] Lookup RPC failed, falling back:", error);
            return super._onCouponScan(code);
        }

        if (!result || (!result.successful && result.payload?.not_found)) {
            // Not a consignment card — fall back to normal coupon handling
            return super._onCouponScan(code);
        }

        if (!result.successful) {
            this.notification.add(
                result.payload?.error_message || "Card validation failed",
                { type: "danger" }
            );
            return;
        }

        // Card found — handle customer validation/auto-set
        const cardData = result.payload;
        const cardPartnerId = cardData.partner_id;

        if (partnerId && cardPartnerId && partnerId !== cardPartnerId) {
            // Order has a different customer than the card owner
            this.notification.add(
                `This card belongs to ${cardData.partner_name}, not the current customer.`,
                { type: "danger" }
            );
            return;
        }

        if (!partnerId && cardPartnerId) {
            // No customer on order — auto-set card owner as customer
            const cardPartner = this.pos.models["res.partner"].get(cardPartnerId);
            if (cardPartner) {
                order.set_partner(cardPartner);
            }
        }

        // Open item selection popup
        this.dialog.add(ConsignCardPopup, {
            cardData,
            getPayload: async (consignSelection) => {
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

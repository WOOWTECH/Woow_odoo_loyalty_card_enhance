/** @odoo-module **/

import { ControlButtons } from "@point_of_sale/app/screens/product_screen/control_buttons/control_buttons";
import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";
import { ConsignCardPopup } from "@woow_loyalty_consign_pos/overrides/components/consign_card_popup/consign_card_popup";
import { ConsignCardListPopup } from "@woow_loyalty_consign_pos/overrides/components/consign_card_list_popup/consign_card_list_popup";

async function addPersistedConsignLines(component, order, consignSelection) {
    const card = component.pos.models["loyalty.card"].get(consignSelection.card_id);
    const selections = consignSelection.lines.map((line) => ({
        line,
        product: component.pos.models["product.product"].get(line.product_id),
    }));
    if (!card || selections.some(({ product }) => !product)) {
        component.notification.add(
            _t("A selected consignment product is not available in this POS. Enable it in Point of Sale and reload the register."),
            { type: "danger" }
        );
        return false;
    }

    for (const { line, product } of selections) {
        await component.pos.addLineToCurrentOrder(
            {
                product_id: product,
                qty: line.qty_redeemed,
                price_unit: 0,
                customer_note: `[${_t("Consign")}] ${line.product_name}`,
                consign_card_id: card,
                consign_covered_qty: line.qty_redeemed,
                is_consign_redemption: true,
            },
            {},
            false
        );
    }
    component.notification.add(
        _t("Added %s consignment item(s) for pickup", selections.length),
        { type: "success" }
    );
    return true;
}

patch(ControlButtons.prototype, {
    async onClickConsignRedemption() {
        const order = this.pos.get_order();
        let partner = order.get_partner();
        if (!partner) {
            await this.pos.selectPartner();
            partner = order.get_partner();
            if (!partner) {
                return;
            }
        }

        let result;
        try {
            result = await this.pos.data.call(
                "pos.config",
                "get_partner_consign_cards",
                [[this.pos.config.id], partner.id]
            );
        } catch (error) {
            console.error("[ConsignCard] get_partner_consign_cards RPC failed:", error);
            this.notification.add(_t("Failed to load consignment cards."), { type: "danger" });
            return;
        }
        if (!result?.successful) {
            this.notification.add(
                result?.payload?.error_message || _t("Failed to load consignment cards."),
                { type: "danger" }
            );
            return;
        }

        const cards = result.payload.cards || [];
        if (!cards.length) {
            this.notification.add(
                _t("%s has no consignment cards available for redemption.", partner.name),
                { type: "warning" }
            );
            return;
        }
        if (cards.length === 1) {
            this._openConsignCardPopup(cards[0]);
            return;
        }
        this.dialog.add(ConsignCardListPopup, {
            partnerName: partner.name,
            cards,
            onCardSelected: (cardData) => this._openConsignCardPopup(cardData),
        });
    },

    _openConsignCardPopup(cardData) {
        this.dialog.add(ConsignCardPopup, {
            cardData,
            getPayload: async (selection) => {
                await addPersistedConsignLines(this, this.pos.get_order(), selection);
            },
        });
    },
});

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
                [[this.pos.config.id], code.base_code, partnerId]
            );
        } catch (error) {
            console.error("[ConsignCard] Lookup RPC failed, falling back:", error);
            return super._onCouponScan(code);
        }
        if (!result || (!result.successful && result.payload?.not_found)) {
            return super._onCouponScan(code);
        }
        if (!result.successful) {
            this.notification.add(
                result.payload?.error_message || _t("Card validation failed"),
                { type: "danger" }
            );
            return;
        }

        const cardData = result.payload;
        if (partnerId && cardData.partner_id && partnerId !== cardData.partner_id) {
            this.notification.add(
                _t("This card belongs to %s, not the current customer.", cardData.partner_name),
                { type: "danger" }
            );
            return;
        }
        if (!partnerId && cardData.partner_id) {
            const cardPartner = this.pos.models["res.partner"].get(cardData.partner_id);
            if (!cardPartner) {
                this.notification.add(_t("The consignment card customer is unavailable."), { type: "danger" });
                return;
            }
            order.set_partner(cardPartner);
        }
        this.dialog.add(ConsignCardPopup, {
            cardData,
            getPayload: async (selection) => {
                await addPersistedConsignLines(this, order, selection);
            },
        });
    },
});

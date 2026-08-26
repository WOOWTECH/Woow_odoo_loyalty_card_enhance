/** @odoo-module **/

import { PosStore } from "@point_of_sale/app/store/pos_store";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(PosStore.prototype, {
    async pay() {
        const order = this.get_order();
        const hasConsignIntent = order?.lines.some(
            (line) => line.consign_card_id && line.consign_covered_qty > 0
        );
        if (hasConsignIntent) {
            if (!navigator.onLine) {
                this.notification.add(
                    _t("Consignment redemption requires an online authorization before payment."),
                    { type: "danger" }
                );
                return;
            }
            try {
                // Persist the draft intent first. The backend authorizes from
                // those exact lines; only a successful sync may open payment.
                await this.syncAllOrders({ orders: [order], throw: true });
            } catch (error) {
                console.error("[ConsignRedemption] Online authorization failed:", error);
                this.notification.add(
                    _t("Consignment authorization failed. The order remains unpaid; refresh the card balance and retry."),
                    { type: "danger" }
                );
                return;
            }
        }
        return super.pay(...arguments);
    },
});

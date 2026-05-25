/** @odoo-module */

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

patch(PaymentScreen.prototype, {
    /**
     * After order is synced to backend, confirm consign redemptions.
     */
    async _postPushOrderResolve(order, order_server_ids) {
        const consignRedemptions = order.uiState.consignRedemptions || [];
        if (consignRedemptions.length > 0 && order_server_ids.length > 0) {
            try {
                for (const consignData of consignRedemptions) {
                    const result = await this.pos.data.call(
                        "pos.order",
                        "confirm_consign_redemptions",
                        [order_server_ids, consignData]
                    );
                    if (!result.successful) {
                        this.dialog.add(AlertDialog, {
                            title: _t("Consignment Redemption Error"),
                            body: result.payload.error_message || _t("Redemption failed"),
                        });
                    }
                }
            } catch (error) {
                console.error("[ConsignRedemption] Failed to confirm redemptions:", error);
                this.dialog.add(AlertDialog, {
                    title: _t("Consignment Redemption Error"),
                    body: _t("Could not connect to server to confirm redemption. Please contact the administrator."),
                });
            }
        }
        return super._postPushOrderResolve(...arguments);
    },
});

/** @odoo-module */

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

patch(PaymentScreen.prototype, {
    /**
     * After order is synced to backend, confirm consign redemptions.
     * Uses the standard _postPushOrderResolve hook (same pattern as l10n_es).
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
                            title: _t("寄品核銷錯誤"),
                            body: result.payload.error_message || _t("核銷失敗"),
                        });
                    }
                }
            } catch {
                this.dialog.add(AlertDialog, {
                    title: _t("寄品核銷錯誤"),
                    body: _t("無法連接伺服器確認核銷，請聯繫管理員。"),
                });
            }
        }
        return super._postPushOrderResolve(...arguments);
    },
});

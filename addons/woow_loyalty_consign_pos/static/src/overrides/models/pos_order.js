/** @odoo-module */

import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    /**
     * Flag orders with consign redemptions for post-push processing.
     * This ensures _postPushOrderResolve is called after sync.
     */
    wait_for_push_order() {
        if ((this.uiState.consignRedemptions || []).length > 0) {
            return true;
        }
        return super.wait_for_push_order(...arguments);
    },
});

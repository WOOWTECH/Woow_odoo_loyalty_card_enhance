/** @odoo-module */

import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class ConsignCardListPopup extends Component {
    static template = "woow_loyalty_consign_pos.ConsignCardListPopup";
    static components = { Dialog };
    static props = {
        partnerName: String,
        cards: Array,
        onCardSelected: Function,
        close: Function,
    };

    get title() {
        return _t("Consignment Redemption - %s", this.props.partnerName);
    }

    selectCard(card) {
        this.props.onCardSelected(card);
        this.props.close();
    }
}

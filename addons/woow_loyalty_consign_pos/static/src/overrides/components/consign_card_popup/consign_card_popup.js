/** @odoo-module */

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class ConsignCardPopup extends Component {
    static template = "woow_loyalty_consign_pos.ConsignCardPopup";
    static components = { Dialog };
    static props = {
        cardData: Object,
        getPayload: Function,
        close: Function,
    };

    setup() {
        this.state = useState({
            lines: this.props.cardData.lines.map((line) => ({
                ...line,
                qty_to_redeem: 0,
            })),
        });
    }

    get title() {
        return _t("Consignment Redemption - %s", this.props.cardData.program_name);
    }

    get subtitle() {
        const { card_code, partner_name } = this.props.cardData;
        return `${partner_name} / ${card_code}`;
    }

    get hasSelection() {
        return this.state.lines.some((l) => l.qty_to_redeem > 0);
    }

    _roundedQty(line, value) {
        const rounding = line.uom_rounding || 1;
        const rounded = Math.round(value / rounding) * rounding;
        return Math.max(0, Math.min(rounded, line.qty_available));
    }

    increment(line) {
        line.qty_to_redeem = this._roundedQty(
            line, line.qty_to_redeem + (line.uom_rounding || 1)
        );
    }

    decrement(line) {
        line.qty_to_redeem = this._roundedQty(
            line, line.qty_to_redeem - (line.uom_rounding || 1)
        );
    }

    setQty(line, ev) {
        const value = Number.parseFloat(ev.target.value) || 0;
        line.qty_to_redeem = this._roundedQty(line, value);
    }

    confirm() {
        const selectedLines = this.state.lines
            .filter((l) => l.qty_to_redeem > 0)
            .map((l) => ({
                consign_line_id: l.id,
                product_id: l.product_id,
                product_name: l.product_name,
                qty_redeemed: l.qty_to_redeem,
                unit_price: l.unit_price,
            }));
        if (selectedLines.length === 0) {
            return;
        }
        this.props.getPayload({
            card_id: this.props.cardData.card_id,
            lines: selectedLines,
        });
        this.props.close();
    }
}

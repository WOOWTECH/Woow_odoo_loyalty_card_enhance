/** @odoo-module */

import { Component, useState } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { _t } from "@web/core/l10n/translation";

export class ConsignSearchPopup extends Component {
    static template = "woow_loyalty_consign_pos.ConsignSearchPopup";
    static components = { Dialog };
    static props = {
        pos: Object,
        onCardSelected: Function,
        close: Function,
    };

    setup() {
        this.state = useState({
            mode: "code",
            codeInput: "",
            customerQuery: "",
            cards: [],
            loading: false,
            error: "",
        });
    }

    get title() {
        return _t("Consignment Redemption");
    }

    switchMode(mode) {
        this.state.mode = mode;
        this.state.error = "";
        this.state.cards = [];
    }

    onCodeKeydown(ev) {
        if (ev.key === "Enter") {
            this.searchByCode();
        }
    }

    onCustomerKeydown(ev) {
        if (ev.key === "Enter") {
            this.searchByCustomer();
        }
    }

    async searchByCode() {
        const code = this.state.codeInput.trim();
        if (!code) return;

        this.state.loading = true;
        this.state.error = "";
        try {
            const result = await this.props.pos.data.call(
                "pos.config",
                "use_consign_card_code",
                [[this.props.pos.config.id], code, false]
            );
            if (result && result.successful) {
                this.props.onCardSelected(result.payload);
                this.props.close();
            } else {
                this.state.error = result?.payload?.error_message || _t("Card not found");
            }
        } catch (error) {
            console.error("[ConsignSearch] Code lookup failed:", error);
            this.state.error = _t("Search failed. Please try again.");
        }
        this.state.loading = false;
    }

    async searchByCustomer() {
        const query = this.state.customerQuery.trim();
        if (!query) return;

        this.state.loading = true;
        this.state.error = "";
        this.state.cards = [];
        try {
            const result = await this.props.pos.data.call(
                "pos.config",
                "search_consign_cards",
                [[this.props.pos.config.id], query]
            );
            if (result && result.successful) {
                this.state.cards = result.payload.cards || [];
                if (this.state.cards.length === 0) {
                    this.state.error = _t("No consignment cards found for this search.");
                }
            } else {
                this.state.error = _t("Search failed.");
            }
        } catch (error) {
            console.error("[ConsignSearch] Customer search failed:", error);
            this.state.error = _t("Search failed. Please try again.");
        }
        this.state.loading = false;
    }

    selectCard(card) {
        this.props.onCardSelected(card);
        this.props.close();
    }
}

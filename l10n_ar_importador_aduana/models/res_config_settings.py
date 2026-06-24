# -*- coding: utf-8 -*-
from odoo import models, fields


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    l10n_ar_dua_account_arancel_id = fields.Many2one(
        related='company_id.l10n_ar_dua_account_arancel_id',
        string='Cuenta Gastos DUA (Arancel)',
        readonly=False,
        help='Cuenta de gastos para ARANCEL (010). Default: 509.71',
    )

    l10n_ar_dua_account_estadistica_id = fields.Many2one(
        related='company_id.l10n_ar_dua_account_estadistica_id',
        string='Cuenta Estadística / SIM',
        readonly=False,
        help='Cuenta para TASA ESTADÍSTICA (011/061) y SIM (500). Default: 509.55',
    )
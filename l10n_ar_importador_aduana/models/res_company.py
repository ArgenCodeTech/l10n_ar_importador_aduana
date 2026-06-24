# -*- coding: utf-8 -*-
from odoo import models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    l10n_ar_dua_account_arancel_id = fields.Many2one(
        'account.account',
        string='Cuenta Gastos DUA (Arancel)',
        help='Cuenta de gastos para ARANCEL (010). Default: 509.71',
    )

    l10n_ar_dua_account_estadistica_id = fields.Many2one(
        'account.account',
        string='Cuenta Estadística / SIM',
        help='Cuenta para TASA ESTADÍSTICA (011/061) y SIM (500). Default: 509.55',
    )
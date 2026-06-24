# -*- coding: utf-8 -*-
{
    'name': 'Argentina Importación Aduana',
    'version': '15.0.3.0.0',
    'category': 'Accounting/Localizations',
    'summary': 'Carga automática de Despachos de Aduana (OM-1993) desde PDF con liquidación fiscal RG 3685',
    'description': """
Módulo comercial para la importación automática de Despachos de Aduana.
Permite subir el Formulario Oficial OM-1993 mediante un asistente (wizard),
extrayendo en memoria y desglosando de forma automática:

- Aranceles y Tasas Estadísticas (Costo — cuentas configurables por empresa)
- IVA Crédito Fiscal (21%)
- Percepción de Ganancias (RG 830)
- Percepciones de Ingresos Brutos Provinciales (Convenio Multilateral)
- ARANCEL SIM IMPO (No Gravado)

Genera la Factura de Proveedor en estado borrador con ajuste atómico quirúrgico
garantizando balance contable perfecto y conformidad con el validador ARCA (RG 3685).
""",
    'author': 'ArgenCode Tech',
    'website': 'https://argencodetech.com',
    'depends': [
        'base',
        'account',
        'l10n_ar',      
    ],
    'data': [
        'data/account_data.xml',
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'wizards/importar_dua_wizard_views.xml',
    ],
    'external_dependencies': {
        'python': ['pdfplumber'],
    },
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
    'demo': [],
    'license': 'OPL-1',
    'price': 850,
    'currency': 'USD',
}

# Argentina Importación Aduana (DUA)

[![License: OPL-1](https://img.shields.io/badge/License-OPL--1-blue.svg)](https://www.odoo.com/documentation/15.0/legal/licenses.html)
[![Odoo Version](https://img.shields.io/badge/Odoo-15.0-purple.svg)](https://www.odoo.com)
[![Localización](https://img.shields.io/badge/Localización-Argentina-lightblue.svg)](https://github.com/ingadhoc)

## Descripción

Automatiza la lectura, interpretación fiscal e inyección contable de los
Despachos de Importación (Formulario OM-1993 / SIM) en Odoo v15.

Reemplaza la carga manual de más de 15 líneas impositivas, eliminando el
error humano y garantizando conformidad con el validador del **Portal IVA
de ARCA (RG 3685)**.

## Funcionalidades

- Extracción automática desde PDF (pdfplumber) del Formulario OM-1993
- Liquidación automática de tributos aduaneros:
  - **ARANCEL (010)** — base imponible gravada con IVA 21%
  - **TASA ESTADÍSTICA (011/061)** — gravada con IVA 21%
  - **ARANCEL SIM IMPO (500)** — no gravado
  - **IVA Crédito Fiscal (415)** — ajuste atómico con base inversa real
  - **Percepción Ganancias (424)** — RG 830
  - **Percepciones IIBB Provinciales** — Convenio Multilateral (hasta 10 jurisdicciones)
- Ajuste atómico quirúrgico post-creación con `check_move_validity=False`
- Balance contable perfecto (Debe = Haber al centavo)
- `tax_base_amount` corregido con base inversa real (Neto × 21% = IVA exacto)
- Exportación TXT conforme RG 3685 (COMPRAS_CBTE.txt + COMPRAS_ALICUOTAS.txt)
- Cuentas contables configurables por empresa vía `res.config.settings`
- Detección automática de impuestos no clasificados (UserError descriptivo)
- Adjunta el PDF original a la factura creada

## Requisitos

### Odoo
- Odoo 15.0 Community o Enterprise
- Localización Argentina (`l10n_ar`) instalada y configurada
- Módulo `account_payment_group` (OCA/ingadhoc) recomendado para pagos

### Python
```
pip install pdfplumber
```

### Configuración previa en Odoo
- Moneda **USD** activa con tipo de cambio vigente
- Cuentas contables **509.71** (Arancel) y **509.55** (Estadística/SIM) creadas
- Impuestos de compras configurados:
  - `IVA 21%` (amount_type=percent, amount=21.0)
  - `Percepción Ganancias Sufrida` (amount_type=fixed, amount=1.00)
  - `Percepción IIBB [Provincia] Sufrida` por cada jurisdicción (amount_type=fixed, amount=1.00)
- Diario de pago en USD (código `MAL01`) — opcional para pago automático

## Instalación

1. Copiar el módulo a `addons/`
2. Reiniciar el servidor Odoo
3. Activar desde Configuración → Aplicaciones → Actualizar lista → Instalar
4. Ir a **Configuración → Aduana DUA** y configurar las cuentas contables

## Uso

1. Ir a **Contabilidad → Proveedores → Importar DUA**
2. Seleccionar el PDF del despacho (Formulario OM-1993)
3. Confirmar proveedor y diario
4. El wizard crea la factura en borrador con todos los tributos liquidados

## Configuración por Empresa

En **Configuración → Aduana DUA**:

| Campo | Default | Descripción |
|-------|---------|-------------|
| Cuenta Gastos DUA (Arancel) | 509.71 | ARANCEL (código 010) |
| Cuenta Estadística / SIM | 509.55 | TASA EST. (011/061) y SIM (500) |

## Exportación RG 3685

Script complementario `exportar_txt_iva.py` para generar los archivos TXT
del Libro de IVA Digital conforme RG 3685:

```bash
python exportar_txt_iva.py \
  --config "ruta/odoo.conf" \
  --database nombre_bd \
  --verbose
```

Genera:
- `COMPRAS_CBTE.txt` — 325 caracteres por registro
- `COMPRAS_ALICUOTAS.txt` — 50 caracteres por registro

## Arquitectura Técnica

### Ajuste Atómico v5.1

```
FASE 1 — Grilla limpia
  invoice_line_ids: solo líneas de gasto real (ARANCEL, TASA EST., SIM)
  Total factura = USD exactos del PDF

FASE 2 — Ajuste quirúrgico post-creación
  PASO A: tasa_efectiva = sum(debit líneas gasto) / sum(amount_currency)
  PASO B: corregir tax_lines con montos reales del PDF × tasa_efectiva
  PASO C: recalcular payable → Debe = Haber al centavo por construcción matemática
```

### Clasificación contable

| Cuenta | Tipo |
|--------|------|
| 1.1.4.04.xxx | IVA Crédito Fiscal |
| 1.1.4.05.xxx | Percepción Ganancias |
| 1.1.4.02.xxx | Percepción IIBB |

## Autor

**ArgenCode Tech**
- Website: https://argencodetech.com
- Licencia: OPL-1

## Historial de Versiones

| Versión | Descripción |
|---------|-------------|
| 15.0.3.0.0 | Cuentas configurables por empresa, UserError impuestos no clasificados, SIM → No Gravado |
| 15.0.2.0.0 | Ajuste atómico quirúrgico completo, tax_base_amount base inversa real |
| 15.0.1.0.0 | Versión inicial |

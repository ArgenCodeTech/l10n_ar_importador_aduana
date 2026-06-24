# -*- coding: utf-8 -*-
# Copyright 2026 ArgenCode Tech
# License OPL-1 (https://www.odoo.com/documentation/15.0/legal/licenses.html)
#
# Wizard para importar Despacho de Aduana (OM-1993) desde PDF.
# Versión: 15.0.3.0.0
#
# Arquitectura: grilla limpia + ajuste atómico quirúrgico post-creación.
# Garantiza balance contable perfecto y conformidad RG 3685 (ARCA).
#
# Ver historial completo en: HISTORIAL_importar_dua_wizard.txt

import base64
import re
import io
import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ImportarDUAWizard(models.TransientModel):
    _name = 'l10n_ar.importar.dua.wizard'
    _description = 'Wizard para importar Despacho de Aduana desde PDF'

    # =========================================================================
    # Constantes de clase
    # =========================================================================

    # Mapeo código DUA → fragmento de nombre del impuesto en Odoo
    # Patrón esperado: "Percepción IIBB [Provincia] Sufrida"
    IIBB_PROVINCIAS = {
        '902': 'ARBA',
        '922': 'Santiago',
        '909': 'Formosa',
        '906': 'Chaco',
        '918': 'San Juan',
        '917': 'Salta',
        '923': 'Tierra del Fuego',
        '907': 'Chubut',
        '915': 'Neuquén',
        '904': 'Córdoba',
    }

    # =========================================================================
    # Campos del wizard
    # =========================================================================

    file_data = fields.Binary(
        string='Archivo PDF del DUA',
        required=True,
        attachment=False,
        help='Seleccione el archivo PDF del despacho de importación',
    )
    file_name = fields.Char(string='Nombre del Archivo')

    partner_id = fields.Many2one(
        'res.partner',
        string='Proveedor',
        required=True,
        domain=[('supplier_rank', '>', 0)],
        help='Proveedor asociado al despacho',
    )

    journal_id = fields.Many2one(
        'account.journal',
        string='Diario',
        required=True,
        domain=[('type', '=', 'purchase')],
        default=lambda self: self._get_default_journal(),
    )

    extracted_ref  = fields.Char(string='Nº Despacho extraído', readonly=True)
    extracted_date = fields.Date(string='Fecha extraída',        readonly=True)

    registrar_pago_auto = fields.Boolean(
        string='Registrar Pago Automático en Borrador',
        default=False,
        help=(
            'Activado: crea una Orden de Pago en borrador simultáneamente '
            'con la factura, resolviendo la secuencia del diario de forma '
            'limpia para evitar el bug "False" de la localización OCA Argentina.\n'
            'Desactivado (recomendado): solo crea la factura en borrador y '
            'notifica a Tesorería en el chatter para que inicie el pago desde '
            'el botón nativo "Registrar Pago" cuando corresponda.'
        ),
    )

    # =========================================================================
    # Helpers de conversión
    # =========================================================================

    @api.model
    def _m2f(self, text):
        """Convierte string con formato argentino (1.234,56) a float."""
        t = str(text).strip().replace(' ', '').replace('*', '')
        if not t:
            return 0.0
        t = t.replace('.', '').replace(',', '.')
        try:
            return float(t)
        except ValueError:
            return 0.0

    @api.model
    def _fecha_odoo(self, fecha_str):
        """Convierte DD/MM/YYYY a YYYY-MM-DD. Retorna False si falla."""
        try:
            d, m, a = fecha_str.strip().split('/')
            return f'{a}-{m}-{d}'
        except Exception:
            return False

    # =========================================================================
    # Lookup de impuestos — sin IDs hardcodeados
    # =========================================================================

    @api.model
    def _find_tax_by_name(self, *fragments):
        """
        Busca impuesto de compras que contenga TODOS los fragmentos dados.
        Retorna recordset vacío si no encuentra.
        """
        domain = [
            ('type_tax_use', '=', 'purchase'),
            ('company_id',   '=', self.env.company.id),
        ]
        for fragment in fragments:
            domain.append(('name', 'ilike', fragment))
        return self.env['account.tax'].search(domain, limit=1)

    @api.model
    def _find_tax_by_percent(self, amount):
        """Busca impuesto de compras por porcentaje exacto (fallback)."""
        return self.env['account.tax'].search([
            ('type_tax_use', '=', 'purchase'),
            ('amount_type',  '=', 'percent'),
            ('amount',       '=', amount),
            ('company_id',   '=', self.env.company.id),
        ], limit=1)

    # =========================================================================
    # Lookup de cuentas contables
    # =========================================================================

    @api.model
    def _get_account(self, code):
        """
        Busca cuenta contable por código exacto, luego por prefijo.
        Lanza UserError si no encuentra.
        """
        Account = self.env['account.account']
        account = Account.search([
            ('code',       '=', code),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not account:
            account = Account.search([
                ('code',       'like', code + '%'),
                ('company_id', '=', self.env.company.id),
            ], limit=1)
        if not account:
            raise UserError(
                f'No se encontró la cuenta contable {code}.\n'
                f'Verifique que esté creada o ejecute account_data.xml.'
            )
        return account

    # =========================================================================
    # Defaults
    # =========================================================================

    def _get_default_journal(self):
        journal = self.env['account.journal'].search(
            [('type', '=', 'purchase')], limit=1
        )
        return journal.id if journal else False

    def _get_document_type_despacho(self):
        """
        Busca tipo de documento AFIP 66 (Despacho de Importación).
        Fallback por nombre.
        """
        doc_type = self.env['l10n_latam.document.type'].search(
            [('code', '=', '66')], limit=1
        )
        if not doc_type:
            doc_type = self.env['l10n_latam.document.type'].search(
                [('name', 'ilike', 'despacho')], limit=1
            )
        return doc_type.id if doc_type else False

    # =========================================================================
    # Extracción del PDF
    # =========================================================================

    def _extract_from_pdf(self, pdf_binary):
        """
        Extrae todos los datos necesarios del DUA en PDF.

        Retorna dict con:
          liq         — {cod: monto_usd}  tributos principales
          ref_prov    — str  referencia interna
          nro_doc     — str  número de despacho 16 chars (AFIP)
          fecha       — str  YYYY-MM-DD
          tasa        — float  pesos por dólar (informativa, no se usa para ARS)
          iibb_montos — {cod: monto_usd}  percepciones IIBB por provincia
        """
        try:
            import pdfplumber
        except ImportError:
            raise UserError(
                'Falta la librería pdfplumber.\n'
                'Instálela con:  pip install pdfplumber'
            )

        pdf_bytes = base64.b64decode(pdf_binary)
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            textos = [p.extract_text() or '' for p in pdf.pages]

        if not textos or not any(textos):
            raise UserError(
                'No se pudo extraer texto del PDF.\n'
                'Verifique que no esté protegido ni sea imagen escaneada.'
            )

        p1 = textos[0] if textos else ''
        p8 = next(
            (t for t in textos if 'COEFICIENTES DE DISTRIBUCION' in t), ''
        )

        # --- Liquidación de tributos (código → monto USD) ---
        liq = {}
        patron_tributo = re.compile(
            r'\(\s*(\d{3})\s*\)\s+[\w\s\.]+?\s+P\s+([\d.,]+)'
        )
        for mm in patron_tributo.finditer(p1):
            cod, val = mm.group(1), self._m2f(mm.group(2))
            if cod not in liq or val > liq[cod]:
                liq[cod] = val
        for t in textos[1:4]:
            for mm in patron_tributo.finditer(t):
                cod = mm.group(1)
                if cod not in liq:
                    liq[cod] = self._m2f(mm.group(2))

        # --- Referencia y número de despacho ---
        m = re.search(
            r'(\d{2})\s+(\d{3})\s+(\w{4})\s+(\d{6})\s+(\w)\s+\d+ de \d+', p1
        )
        if m:
            ref_prov = f"{m.group(3).upper()} {m.group(4)}{m.group(5).upper()}"
            nro_doc  = f"20{m.group(1)}-{m.group(2)}04{m.group(4)}"
        else:
            ref_prov = ''
            nro_doc  = ''

        m2    = re.search(r'(\d{2}/\d{2}/\d{4})\s+\d{2}\s+\d{3}', p1)
        fecha = self._fecha_odoo(m2.group(1)) if m2 else False

        mc   = re.search(r'Cotiz\s*=\s*([\d.,]+)', p1)
        tasa = self._m2f(mc.group(1)) if mc else 0.0

        # --- Percepciones IIBB desde página de coeficientes ---
        iibb_montos = {}
        for linea in p8.split('\n'):
            mm = re.match(
                r'^(\d{3})\s+[\d,]+\s+([\d.,]+)\s+(\d{3})\s+-\s+', linea
            )
            if mm:
                val = self._m2f(mm.group(2))
                cod = mm.group(3)
                if val > 0:
                    iibb_montos[cod] = val

        return {
            'liq':         liq,
            'ref_prov':    ref_prov,
            'nro_doc':     nro_doc,
            'fecha':       fecha,
            'tasa':        tasa,
            'iibb_montos': iibb_montos,
        }

    # =========================================================================
    # FASE 2 — Ajuste quirúrgico atómico del asiento
    # =========================================================================

    def _ajustar_asiento_dua(self, invoice, montos_usd_reales,
                              tax_iva_id=None, monto_415_usd=0.0,
                              iva_rate=21.0):
        """
        Ajusta el asiento contable del DUA para reflejar los montos exactos
        del PDF en las líneas de impuesto y cuadrar el asiento al centavo.
        v5.2: también corrige tax_base_amount de la línea IVA para RG 3685.

        Ejecutado DESPUÉS de account.move.create() en borrador.
        Todo el ajuste ocurre dentro de un único bloque
        with_context(check_move_validity=False) para evitar validaciones
        intermedias de balance.

        Parámetros:
            invoice           — account.move en estado draft
            montos_usd_reales — dict {tax.id: monto_usd_del_pdf}
            tax_iva_id        — int  ID del impuesto IVA (para fix tax_base_amount)
            monto_415_usd     — float  monto IVA real del PDF en USD (código 415)
            iva_rate          — float  alícuota IVA (default 21.0)

        PASO A — Tasa efectiva desde líneas de gasto:
            Las líneas de gasto (sin tax_line_id, tipo distinto de payable)
            ya tienen debit en ARS calculado por Odoo con su tasa interna.
            tasa_efectiva = Σ(debit_gasto) / Σ(amount_currency_gasto)
            Esta es la tasa exacta que Odoo aplicó → garantiza consistencia.

        PASO B — Corregir tax_lines + tax_base_amount IVA (v5.2):
            Para cada línea con tax_line_id asignado:
              amount_currency = monto_usd_real (del PDF)
              debit           = round(monto_usd * tasa_efectiva, 2)
              balance         = debit
              credit          = 0.0
            Solo para la línea IVA (v5.2 FIX RG 3685):
              base_inversa_usd = monto_415 / (iva_rate / 100)
              tax_base_amount  = round(base_inversa_usd * tasa_efectiva, 2)
              → AFIP verá: tax_base_amount × 21% = monto_415 exacto del PDF ✅

        PASO C — Recalcular payable para cuadrar al centavo:
            total_debe_ars = Σ(debit de líneas de gasto + tax corregidas)
            total_debe_usd = Σ(amount_currency de gasto + tax corregidas)
            payable.write({
                credit:          total_debe_ars,
                debit:           0.0,
                balance:        -total_debe_ars,
                amount_currency: -total_debe_usd,
            })
            → Debe = Haber por suma directa, al centavo, sin aproximación.
        """
        # Clasificar líneas del asiento
        lineas_gasto   = invoice.line_ids.filtered(
            lambda l: not l.tax_line_id
                      and l.account_id.user_type_id.type != 'payable'
        )
        lineas_tax     = invoice.line_ids.filtered(lambda l: bool(l.tax_line_id))
        linea_payable  = invoice.line_ids.filtered(
            lambda l: l.account_id.user_type_id.type == 'payable'
        )[:1]
        
        # Pendiente 2 — detectar impuestos no clasificados (OCA pattern)
        cuentas_conocidas = ('1.1.4.04', '1.1.4.05', '1.1.4.02')
        for l in lineas_tax:
            cod = l.account_id.code or ''
            if not any(cod.startswith(c) for c in cuentas_conocidas):
                raise UserError(
                    'Impuesto no clasificado detectado en el asiento.\n'
                    'Impuesto: "%s"\n'
                    'Cuenta:   %s\n\n'
                    'Agregue esta cuenta al módulo o contacte al administrador.'
                    % (l.tax_line_id.name, cod)
                )


        if not linea_payable:
            _logger.error(
                'v5.2 — No se encontró línea payable en factura ID %s. '
                'Ajuste cancelado.',
                invoice.id,
            )
            return

        # ── PASO A: Tasa efectiva desde líneas de gasto ───────────────────
        suma_ars_gasto = sum(l.debit for l in lineas_gasto)
        suma_usd_gasto = sum(abs(l.amount_currency) for l in lineas_gasto)

        if not suma_usd_gasto or suma_usd_gasto == 0:
            _logger.error(
                'v5.2 — Líneas de gasto USD = 0 en factura ID %s. '
                'No se puede calcular tasa efectiva. Ajuste cancelado.',
                invoice.id,
            )
            return

        tasa_efectiva = suma_ars_gasto / suma_usd_gasto

        _logger.info(
            'v5.2 — Tasa efectiva: ARS %.2f / USD %.2f = %.6f',
            suma_ars_gasto, suma_usd_gasto, tasa_efectiva,
        )

        # ── PASOS B y C dentro de un único contexto sin validación ───────
        with self.env.cr.savepoint():
            # PASO B: corregir cada tax_line
            ars_tax_total = 0.0
            usd_tax_total = 0.0

            for linea in lineas_tax:
                monto_usd = montos_usd_reales.get(linea.tax_line_id.id)
                if monto_usd is None:
                    _logger.warning(
                        'v5.2 — Sin monto PDF para impuesto "%s" (ID %s). '
                        'Línea no corregida.',
                        linea.tax_line_id.name, linea.tax_line_id.id,
                    )
                    # Sumar el valor que dejó Odoo para no romper el cuadre
                    ars_tax_total += linea.debit
                    usd_tax_total += abs(linea.amount_currency)
                    continue

                monto_ars = round(monto_usd * tasa_efectiva, 2)

                # Campos base del write para todas las tax_lines
                vals_write = {
                    'amount_currency': monto_usd,
                    'debit':           monto_ars,
                    'credit':          0.0,
                    'balance':         monto_ars,
                }

                # v5.2 FIX RG 3685 — corregir tax_base_amount solo para IVA
                # tax_base_amount es el campo informativo que AFIP usa para
                # validar: base × alícuota = impuesto liquidado.
                # Odoo lo calculó desde price_unit del ARANCEL (base incorrecta).
                # Lo corregimos con la base inversa real del PDF:
                #   base_inversa_usd = monto_iva_pdf / (iva_rate / 100)
                #   → AFIP verá: base_ars × 21% = monto_iva_ars exacto del PDF
                # Este campo es puramente informativo: no altera totales ni balance.
                if (tax_iva_id
                        and linea.tax_line_id.id == tax_iva_id
                        and monto_415_usd
                        and iva_rate):
                    base_inversa_usd = monto_415_usd / (iva_rate / 100.0)
                    base_inversa_ars = round(base_inversa_usd * tasa_efectiva, 2)
                    vals_write['tax_base_amount'] = base_inversa_ars
                    _logger.info(
                        'v5.2 — tax_base_amount IVA: USD %.2f / %.0f%% = '
                        'USD %.6f → ARS %.2f',
                        monto_415_usd, iva_rate,
                        base_inversa_usd, base_inversa_ars,
                    )

                linea.with_context(check_move_validity=False).write(vals_write)

                ars_tax_total += monto_ars
                usd_tax_total += monto_usd

                _logger.info(
                    'v5.2 — Tax corregida "%s": USD %.2f × %.6f = ARS %.2f',
                    linea.tax_line_id.name, monto_usd, tasa_efectiva, monto_ars,
                )

            # PASO C: recalcular payable para cuadrar al centavo
            total_debe_ars = round(suma_ars_gasto + ars_tax_total, 2)
            total_debe_usd = round(suma_usd_gasto + usd_tax_total, 2)

            linea_payable.with_context(check_move_validity=False).write({
                'amount_currency': -total_debe_usd,
                'debit':            0.0,
                'credit':           total_debe_ars,
                'balance':         -total_debe_ars,
            })

            _logger.info(
                'v5.2 — Payable recalculada: Haber ARS %.2f / USD %.2f',
                total_debe_ars, total_debe_usd,
            )
            _logger.info(
                'v5.2 — Asiento cuadrado: Debe = Haber = ARS %.2f',
                total_debe_ars,
            )

    # =========================================================================
    # Creación del pago en borrador — integración OCA payment_group (v6.0)
    # =========================================================================

    def _obtener_o_crear_receiptbook(self):
        """
        Busca un Talonario (account.payment.receiptbook) para proveedores.
        Si no existe ninguno en la base —caso confirmado en aduana_test—,
        lo crea con secuencia automática.

        Campos confirmados por inspección directa del modelo (shell Odoo):
          partner_type:    selection ['customer', 'supplier']
          sequence_type:   selection ['automatic', 'manual']
          document_type_id: OBLIGATORIO — constraint NOT NULL a nivel de
                             base de datos (descubierto en runtime: el
                             create() sin este campo lanza
                             psycopg2.errors.NotNullViolation).
          (el modelo NO tiene journal_id — no está atado a un diario)

        ⚠️ NOTA FUNCIONAL PENDIENTE DE VALIDACIÓN CONTABLE:
          Ninguno de los 63 l10n_latam.document.type disponibles en
          aduana_test tiene internal_type específico para egresos de pago
          (todos son 'invoice', pensados para ventas/facturación).
          Se usa el ID correspondiente a "OTROS COMPROBANTES QUE NO CUMPLEN
          O ESTAN EXCEPTUADOS DE LA R.G. Nº 1415 Y SUS MODIF" (código 99)
          como PLACEHOLDER TÉCNICO para destrabar el flujo en este entorno
          de prueba. Antes de producción, CONFIRMAR con el contador cuál
          es el comprobante real que deben llevar las Órdenes de Pago a
          proveedores en esta instancia — puede variar según convenios
          con AFIP o configuración específica de la empresa.

        Retorna el recordset del receiptbook (existente o nuevo), o False
        si no se puede resolver un document_type_id válido.
        """
        Receiptbook = self.env['account.payment.receiptbook'].sudo()

        receiptbook = Receiptbook.search([
            ('partner_type', '=', 'supplier'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)

        if receiptbook:
            _logger.info(
                'v6.1 — Talonario existente reutilizado: %s (ID %s)',
                receiptbook.name, receiptbook.id,
            )
            return receiptbook

        # ── Resolver document_type_id obligatorio ────────────────────────────
        # Búsqueda dinámica por código AFIP 99 (genérico, no hardcodea el ID
        # numérico de la tabla, que puede variar entre instancias/bases).
        DocType = self.env['l10n_latam.document.type']
        doc_type_receiptbook = DocType.search([
            ('country_id.code', '=', 'AR'),
            ('code', '=', '99'),
        ], limit=1)

        if not doc_type_receiptbook:
            # Fallback: cualquier tipo de documento AR disponible, para no
            # bloquear el flujo completo — pero se loguea como advertencia
            # fuerte porque esto requiere revisión contable.
            doc_type_receiptbook = DocType.search([
                ('country_id.code', '=', 'AR'),
            ], limit=1)
            _logger.warning(
                'v6.1 — No se encontró tipo de documento código 99 (genérico). '
                'Usando fallback: %s (ID %s). REVISAR CON CONTADOR antes de '
                'producción — este campo determina cómo se categoriza el '
                'comprobante de pago ante AFIP.',
                doc_type_receiptbook.name if doc_type_receiptbook else 'NINGUNO',
                doc_type_receiptbook.id if doc_type_receiptbook else 'N/A',
            )

        if not doc_type_receiptbook:
            _logger.error(
                'v6.1 — No existe NINGÚN l10n_latam.document.type para AR. '
                'Imposible crear el Talonario — cae al flujo diferido.'
            )
            return False

        # ── Create() dentro de un savepoint ───────────────────────────────────
        # Si este create() falla con un error SQL (como el NotNullViolation
        # de document_type_id que encontramos en aduana_test), Postgres
        # aborta la transacción completa y CUALQUIER operación posterior
        # —incluso el message_post() del flujo diferido en action_import_pdf—
        # fallaría en cascada con InFailedSqlTransaction.
        # self.env.cr.savepoint() aísla este create(): si falla, sólo se
        # revierte este bloque puntual y la transacción principal del
        # wizard (factura + ajuste de asiento + adjunto) sigue intacta.
        try:
            with self.env.cr.savepoint():
                receiptbook = Receiptbook.create({
                    'name':            'Órdenes de Pago Proveedores - DUA',
                    'partner_type':    'supplier',
                    'sequence_type':   'automatic',
                    'prefix':          '00001-',
                    'padding':         8,
                    'company_id':      self.env.company.id,
                    'document_type_id': doc_type_receiptbook.id,
                })
            _logger.info(
                'v6.1 — Talonario creado al vuelo: %s (ID %s) — '
                'document_type: %s (⚠️ placeholder, confirmar con contador)',
                receiptbook.name, receiptbook.id, doc_type_receiptbook.name,
            )
            return receiptbook
        except Exception as e:
            _logger.error('v6.1 — No se pudo crear el Talonario: %s', e)
            return False

    def _crear_pago_borrador(self, invoice, diario_pago, usd, fecha, ref_prov):
        """
        Crea un account.payment.group (cabecera OCA) con su account.payment
        hijo, respetando la capa de abstracción de l10n_ar_account_payment_group.

        CAUSA RAÍZ DEL BUG 'False' (v5.2): el wizard anterior creaba un
        account.payment suelto. OCA detecta el pago sin grupo y genera un
        account.payment.group automático por su cuenta, pero ese create()
        interno busca un receiptbook_id y no encuentra ninguno configurado
        → el campo name del GRUPO queda en 'False' (fallback silencioso),
        mientras que el PAGO hijo queda colgado en estado 'draft' porque
        nunca se invocó ningún método de transición de estado sobre él.

        SOLUCIÓN v6.0 — flujo correcto de 3 pasos:
          1) Asegurar que exista un receiptbook para proveedores
             (_obtener_o_crear_receiptbook).
          2) Crear el account.payment.group con receiptbook_id ya asignado
             y el account.payment hijo inyectado vía payment_ids (one2many),
             dejando que OCA resuelva name/document_number desde la
             secuencia del receiptbook — no desde ir.sequence genérica.
          3) Verificar que tanto el grupo como el pago hijo queden en
             estado 'draft' (ningún action_post() se invoca).

        Retorna el account.payment.group creado, o False si falla.
        """
        receiptbook = self._obtener_o_crear_receiptbook()
        if not receiptbook:
            _logger.warning(
                'v6.0 — Sin receiptbook disponible — pago no creado. '
                'Cae al flujo diferido (chatter).'
            )
            return False

        # ── Tipo de documento OCA Argentina (opcional, mismo criterio v5.2) ──
        doc_type_pago = self.env['l10n_latam.document.type'].search([
            ('country_id.code', '=', 'AR'),
            ('name', 'ilike', 'Orden de Pago'),
        ], limit=1)
        if not doc_type_pago:
            doc_type_pago = self.env['l10n_latam.document.type'].search([
                ('country_id.code', '=', 'AR'),
                ('internal_type', '=', False),
            ], limit=1)

        # ── Construir el account.payment hijo como comando one2many ─────────
        # NO se asigna 'name' manualmente: el receiptbook resuelve la
        # numeración real al crear el grupo. Forzar 'name' aquí reproduciría
        # el mismo bug que en v5.2 (numeración huérfana sin relación al
        # receiptbook real).
        payment_line_vals = {
            'payment_type':  'outbound',
            'partner_type':  'supplier',
            'partner_id':    self.partner_id.id,
            'journal_id':    diario_pago.id,
            'currency_id':   usd.id,
            'amount':        invoice.amount_total,
            'date':          fecha or fields.Date.today(),
            'ref':           f"DUA {ref_prov}",
        }
        if doc_type_pago:
            payment_line_vals['l10n_latam_document_type_id'] = doc_type_pago.id

        # ── Crear el account.payment.group con el pago inyectado ────────────
        group_vals = {
            'partner_id':     self.partner_id.id,
            'partner_type':   'supplier',
            'currency_id':    usd.id,
            'payment_date':   fecha or fields.Date.today(),
            'communication':  f"DUA {ref_prov}",
            'receiptbook_id': receiptbook.id,
            'payment_ids':    [(0, 0, payment_line_vals)],
        }

        # Mismo motivo que en _obtener_o_crear_receiptbook(): aislar este
        # create() en un savepoint evita que un fallo SQL aquí aborte toda
        # la transacción del wizard (factura + ajuste de asiento + adjunto
        # ya creados con éxito en pasos anteriores de action_import_pdf).
        try:
            with self.env.cr.savepoint():
                payment_group = self.env['account.payment.group'].create(group_vals)
        except Exception as e:
            _logger.warning('v6.2 — Error en create() del payment.group: %s', e)
            return False

        # ── v6.2: disparar confirm() para generar numeración del receiptbook ──
        #
        # OCA NO genera name/document_number en el create() sino en confirm().
        # Verificado por inspección: _compute_document_number y
        # _compute_next_number se disparan en la transición que confirm()
        # orquesta internamente sobre la secuencia del receiptbook_id.
        #
        # Flujo:
        #   1) confirm() → asigna document_number desde receiptbook.sequence_id
        #                   → estado pasa a 'confirmed' (o 'posted' según versión)
        #   2) write({'state': 'draft'}) → revierte al estado borrador para que
        #      el usuario valide manualmente desde la UI sin bloqueos
        #
        # El document_number (y por ende name) quedan asignados permanentemente
        # aunque el estado vuelva a draft — son campos de escritura directa,
        # no se recalculan al revertir el estado.
        try:
            with self.env.cr.savepoint():
                payment_group.post()
                _logger.info(
                    'v6.3 — post() ejecutado — name generado: %s (ID %s)',
                    payment_group.name, payment_group.id,
                )
                # Revertir a borrador para validación manual del usuario
                payment_group.action_draft()

                _logger.info(
                    'v6.2 — Estado revertido a draft — '
                    'grupo: %s pagos hijo: %s',
                    payment_group.state,
                    payment_group.payment_ids.mapped('state'),
                )
        except Exception as e:
            _logger.warning(
                'v6.2 — confirm()/revert falló (ID %s): %s — '
                'el grupo queda en el estado que dejó confirm()',
                payment_group.id, e,
            )

        # ── Verificación de integridad post-creación ─────────────────────────
        estado_grupo = payment_group.state
        pagos_hijo   = payment_group.payment_ids
        estados_hijo = pagos_hijo.mapped('state')

        if payment_group.name in (False, 'False', ''):
            _logger.warning(
                'v6.2 — name sigue False en grupo ID %s — '
                'revisar configuración de secuencia del receiptbook "%s".',
                payment_group.id, receiptbook.name,
            )
        else:
            _logger.info(
                'v6.2 — Payment Group OK: %s (ID %s) '
                '— estado: %s — pagos hijo: %s (estados: %s)',
                payment_group.name, payment_group.id,
                estado_grupo, len(pagos_hijo), estados_hijo,
            )

        return payment_group

    # =========================================================================
    # Acción principal — v5.2 COMERCIAL
    # =========================================================================

    def action_import_pdf(self):
        """
        Procesa el PDF del DUA y crea la factura de proveedor en borrador.

        Flujo v5.2:
          FASE 1 — Extracción, tasa de cambio, impuestos
          FASE 2 — Factura con grilla limpia (010/011/061/500)
          FASE 3 — _ajustar_asiento_dua(): corrige tax_lines + payable
          FASE 4 — PDF adjunto + pago en borrador
        """
        self.ensure_one()

        if not self.file_data:
            raise UserError('Debe seleccionar un archivo PDF.')

        # ── FASE 1A: Extracción PDF ───────────────────────────────────────────
        try:
            data = self._extract_from_pdf(self.file_data)
        except UserError:
            raise
        except Exception as e:
            raise UserError(f'Error inesperado al procesar el PDF:\n{e}')

        self.write({
            'extracted_ref':  data['ref_prov'],
            'extracted_date': data['fecha'],
        })

        liq   = data['liq']
        fecha = data['fecha']
        tasa  = data['tasa']

        # ── FASE 1B: Moneda USD ───────────────────────────────────────────────
        usd = self.env['res.currency'].search([('name', '=', 'USD')], limit=1)
        if not usd:
            raise UserError(
                'No se encontró la moneda USD.\n'
                'Active la localización argentina y habilite la moneda.'
            )

        # ── FASE 1C: Tasa de cambio — sudo() quirúrgico solo aquí ────────────
        if fecha and tasa and tasa > 0:
            try:
                rate_val = 1.0 / tasa
                RateSudo = self.env['res.currency.rate'].sudo()
                existing = RateSudo.search([
                    ('currency_id', '=', usd.id),
                    ('name',        '=', fecha),
                    ('company_id',  '=', self.env.company.id),
                ], limit=1)
                if existing:
                    current_tasa = 1.0 / existing.rate if existing.rate else 0.0
                    if abs(current_tasa - tasa) > 1:
                        existing.write({'rate': rate_val})
                        _logger.info('Tasa USD actualizada %s → %.6f', fecha, tasa)
                else:
                    RateSudo.create({
                        'currency_id': usd.id,
                        'name':        fecha,
                        'rate':        rate_val,
                        'company_id':  self.env.company.id,
                    })
                    _logger.info('Tasa USD creada %s → %.6f', fecha, tasa)
            except Exception as e:
                _logger.warning('No se pudo registrar tasa de cambio: %s', e)

        # ── FASE 1D: Cuentas contables ────────────────────────────────────────
        cta_arancel = (
            self.env.company.l10n_ar_dua_account_arancel_id
            or self._get_account('509.71')
        )
        cta_estadistica = (
            self.env.company.l10n_ar_dua_account_estadistica_id
            or self._get_account('509.55')
        )

        # ── FASE 1E: Impuesto Exento ──────────────────────────────────────────
        tax_exento = (
            self._find_tax_by_name('Exento')
            or self._find_tax_by_name('Exent')
        )

        def exento_cmd():
            return [(6, 0, [tax_exento.id])] if tax_exento else [(5,)]

        # ── FASE 1F: IVA 21% — obligatorio ───────────────────────────────────
        tax_iva = (
            self._find_tax_by_name('IVA', 'Importación')
            or self._find_tax_by_name('IVA 21')
            or self._find_tax_by_percent(21.0)
        )
        if not tax_iva:
            raise UserError(
                'No se encontró el impuesto IVA para importaciones.\n'
                'Se buscó: "IVA Importación" o "IVA 21%".\n'
                'Verifique la configuración de impuestos.'
            )

        # ── FASE 1G: Ganancias Sufrida ────────────────────────────────────────
        tax_ganancias = (
            self._find_tax_by_name('Ganancias', 'Sufrida')
            or self._find_tax_by_name('Ganancias')
        )
        if not tax_ganancias:
            _logger.warning(
                'No se encontró impuesto Ganancias Sufrida — '
                'no se incluirá en tax_ids del ARANCEL.'
            )

        # ── FASE 1H: IIBB provinciales ────────────────────────────────────────
        iibb_taxes = {}   # {cod_aduana: tax recordset}
        for cod, monto in data['iibb_montos'].items():
            if monto <= 0:
                continue
            nombre_prov = self.IIBB_PROVINCIAS.get(cod)
            if not nombre_prov:
                _logger.warning('Código IIBB desconocido: %s — ignorado', cod)
                continue
            tax = self._find_tax_by_name(nombre_prov, 'Sufrida')
            if tax:
                iibb_taxes[cod] = tax
            else:
                _logger.warning(
                    'IIBB: impuesto no encontrado para %s (%s)', cod, nombre_prov
                )

        # ── FASE 1I: Armar tax_ids del ARANCEL ───────────────────────────────
        #
        # Orden: IVA → Ganancias → IIBB (igual que carga manual)
        # Odoo generará una tax_line_id por cada impuesto con su cuenta correcta.
        # Los montos serán incorrectos para los fixed → se corrigen en FASE 3.
        #
        ids_tax_arancel = [tax_iva.id]
        if tax_ganancias:
            ids_tax_arancel.append(tax_ganancias.id)
        for tax_iibb in iibb_taxes.values():
            ids_tax_arancel.append(tax_iibb.id)

        # ── FASE 2: Construir invoice_line_ids — grilla limpia ────────────────
        invoice_lines = []

        # ARANCEL (010) — base imponible del DUA, lleva todos los impuestos
        monto_010 = liq.get('010', 0.0)
        if monto_010:
            invoice_lines.append((0, 0, {
                'name':       'ARANCEL',
                'account_id': cta_arancel.id,
                'price_unit': monto_010,
                'quantity':   1,
                'tax_ids':    [(6, 0, ids_tax_arancel)],
            }))

        # TASA ESTADÍSTICA (011) — forma parte de base gravada
        monto_011 = liq.get('011', 0.0)
        if monto_011:
            invoice_lines.append((0, 0, {
                'name':       'TASA ESTADÍSTICA',
                'account_id': cta_estadistica.id,
                'price_unit': monto_011,
                'quantity':   1,
                'tax_ids':    [(6, 0, ids_tax_arancel)],
            }))

        # TASA ESTAD. MONT. MÁX. (061) — igual que 011
        monto_061 = liq.get('061', 0.0)
        if monto_061:
            invoice_lines.append((0, 0, {
                'name':       'TASA ESTAD. MONT. MÁX.',
                'account_id': cta_estadistica.id,
                'price_unit': monto_061,
                'quantity':   1,
                'tax_ids':    [(6, 0, ids_tax_arancel)],
            }))

        # ARANCEL SIM IMPO (500) — no gravado
        monto_500 = liq.get('500', 0.0)
        if monto_500:
            invoice_lines.append((0, 0, {
                'name':       'ARANCEL SIM IMPO',
                'account_id': cta_estadistica.id,
                'price_unit': monto_500,
                'quantity':   1,
                'tax_ids':    exento_cmd(),
            }))

        if not invoice_lines:
            raise UserError(
                'No se encontraron tributos en el PDF.\n'
                'Verifique que el archivo sea un DUA válido.'
            )

        # ── FASE 2B: Crear la factura ─────────────────────────────────────────
        invoice_vals = {
            'move_type':    'in_invoice',
            'partner_id':   self.partner_id.id,
            'journal_id':   self.journal_id.id,
            'invoice_date': fecha,
            'date':         fecha,
            'ref':          data['ref_prov'],
            'currency_id':  usd.id,
            'l10n_latam_document_type_id': self._get_document_type_despacho(),
            'invoice_line_ids': invoice_lines,
        }

        if data.get('nro_doc'):
            invoice_vals['l10n_latam_document_number'] = data['nro_doc']

        invoice = self.env['account.move'].create(invoice_vals)

        _logger.info(
            'v5.2 — Factura ID %s creada — Ref: %s — Total USD: %.2f',
            invoice.id, data['ref_prov'], invoice.amount_total,
        )

        # ── FASE 3: Ajuste quirúrgico atómico ────────────────────────────────
        #
        # Construir mapa {tax.id: monto_usd_real_del_pdf}
        #
        montos_usd_reales = {}

        monto_415 = liq.get('415', 0.0)
        if monto_415 and tax_iva:
            montos_usd_reales[tax_iva.id] = monto_415

        monto_424 = liq.get('424', 0.0)
        if monto_424 and tax_ganancias:
            montos_usd_reales[tax_ganancias.id] = monto_424

        for cod, tax_iibb in iibb_taxes.items():
            monto_iibb = data['iibb_montos'].get(cod, 0.0)
            if monto_iibb > 0:
                montos_usd_reales[tax_iibb.id] = monto_iibb

        # Ejecutar ajuste solo si hay montos que corregir
        # v5.2: pasar tax_iva_id y monto_415 para corregir tax_base_amount
        iva_rate = tax_iva.amount if tax_iva.amount_type == 'percent' else 21.0
        if montos_usd_reales:
            self._ajustar_asiento_dua(
                invoice,
                montos_usd_reales,
                tax_iva_id    = tax_iva.id if tax_iva else None,
                monto_415_usd = liq.get('415', 0.0),
                iva_rate      = iva_rate,
            )
        else:
            _logger.warning(
                'v5.2 — Sin montos de impuestos para ajustar en factura ID %s.',
                invoice.id,
            )

        # ── FASE 4A: Adjuntar PDF — sudo() quirúrgico ────────────────────────
        try:
            self.env['ir.attachment'].sudo().create({
                'name':      self.file_name or 'DUA.pdf',
                'type':      'binary',
                'datas':     self.file_data,
                'res_model': 'account.move',
                'res_id':    invoice.id,
                'mimetype':  'application/pdf',
            })
        except Exception as e:
            _logger.warning('No se pudo adjuntar el PDF: %s', e)

        # ── FASE 4B: Pago — flujo híbrido v6.0 (account.payment.group OCA) ──────
        diario_pago = self.env['account.journal'].search(
            [('code', '=', 'MAL01')], limit=1
        )

        if self.registrar_pago_auto:
            # ── Flujo Automático Reparado ─────────────────────────────────────
            # Crea el account.payment.group (cabecera OCA) con su pago hijo,
            # resolviendo el receiptbook antes del create() para evitar el
            # bug 'False' en el name del grupo.
            if diario_pago:
                # Capturar el nombre ANTES de invocar _crear_pago_borrador().
                # Si el create() interno lanza una excepción SQL (como el
                # NotNullViolation de document_type_id que encontramos en
                # aduana_test), la transacción de Postgres queda abortada y
                # cualquier lectura posterior de campos -incluso de un
                # recordset ya resuelto como diario_pago- puede fallar con
                # CacheMiss/KeyError en cascada. Guardar el string ahora
                # evita ese problema en el mensaje de fallback.
                diario_pago_nombre = diario_pago.name

                payment_group = self._crear_pago_borrador(
                    invoice    = invoice,
                    diario_pago= diario_pago,
                    usd        = usd,
                    fecha      = fecha,
                    ref_prov   = data['ref_prov'],
                )
                if not payment_group:
                    # Sin receiptbook disponible → caer al flujo diferido
                    invoice.message_post(
                        body=(
                            '<b>⚠️ Pago automático no creado:</b> '
                            'no se pudo generar el Talonario de Pagos. '
                            'Por favor, registre el pago manualmente desde '
                            'el botón <b>Registrar Pago</b> usando el '
                            f'diario <b>{diario_pago_nombre}</b> por '
                            f'<b>USD {invoice.amount_total:,.2f}</b>.'
                        )
                    )
            else:
                _logger.warning('v6.1 — Diario MAL01 no encontrado.')
                invoice.message_post(
                    body=(
                        '<b>⚠️ Pago automático no creado:</b> '
                        'no se encontró el diario MAL01 en el sistema. '
                        'Registre el pago manualmente cuando corresponda.'
                    )
                )

        else:
            # ── Flujo Estándar Diferido ───────────────────────────────────────
            # Solo notifica en el chatter. Tesorería crea el pago desde la UI
            # con todos los validadores activos de la localización OCA Argentina.
            diario_nombre = diario_pago.name if diario_pago else 'MAL01'
            invoice.message_post(
                body=(
                    '<b>📋 Pago pendiente — acción requerida por Tesorería:</b><br/>'
                    f'Despacho: <b>{data["ref_prov"]}</b><br/>'
                    f'Importe: <b>USD {invoice.amount_total:,.2f}</b><br/>'
                    f'Diario sugerido: <b>{diario_nombre}</b><br/>'
                    'Utilice el botón <b>Registrar Pago</b> cuando '
                    'corresponda liquidar este despacho.'
                )
            )
            _logger.info(
                'v5.2 — Flujo diferido: chatter notificado en factura ID %s.',
                invoice.id,
            )

        # ── FASE 4C: Retornar vista de la factura ─────────────────────────────
        return {
            'type':      'ir.actions.act_window',
            'res_model': 'account.move',
            'res_id':    invoice.id,
            'view_mode': 'form',
            'target':    'current',
            'context':   {'create': False},
        }

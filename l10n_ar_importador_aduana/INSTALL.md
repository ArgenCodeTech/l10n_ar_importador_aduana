# Argentina Importación Aduana — Guía de Instalación y Configuración

## Description

Módulo comercial para la importación automática de Despachos de Aduana (Formulario OM-1993).
Procesa el PDF del despacho y genera la Factura de Proveedor en borrador con desglose automático de:

- Arancel de Importación (509.71 — IVA 21%)
- Tasa Estadística (509.55 — IVA 21%)
- SIM IMPO (509.55 — No Gravado)
- IVA Crédito Fiscal (21%)
- Percepción Ganancias (RG 830)
- Percepciones IIBB provinciales (Convenio Multilateral)

Genera asiento con balance contable perfecto y conformidad con el validador ARCA (RG 3685).

---

## Installation

### Paso 1 — Instalar la dependencia Python `pdfplumber`

**En Windows — CMD como Administrador:**

```cmd
"C:\Program Files\Odoo 15.0.x\python\python.exe" -m pip install pdfplumber
```

> Reemplazá `15.0.x` por la versión exacta de tu instalación. Si ese Python no funciona, usá:
> `"C:\Program Files\Odoo 15.0.x\server\venv\Scripts\python.exe" -m pip install pdfplumber`

**En Linux (Ubuntu/Debian):**

```bash
sudo /opt/odoo/venv/bin/pip install pdfplumber
```

**Verificación:**

```bash
python -c "import pdfplumber; print(pdfplumber.__version__)"
```

### Paso 2 — Copiar el módulo al directorio de addons

**Windows:**
```
C:\Program Files\Odoo 15.0.x\server\odoo\addons\l10n_ar_importador_aduana\
```

**Linux:**
```
/opt/odoo/addons/l10n_ar_importador_aduana/
```

### Paso 3 — Reiniciar el servicio Odoo

**Windows — Servicios (Panel de Control → Herramientas Administrativas → Servicios):**

Buscar el servicio "Odoo" → clic derecho → **Reiniciar**

**Linux:**
```bash
sudo systemctl restart odoo
```

### Paso 4 — Activar modo desarrollador

**Ajustes → (scroll al pie) → Activar modo desarrollador**

> El ícono del escarabajo en la barra superior confirma que está activo.

### Paso 5 — Instalar módulos requeridos

Ir a **Aplicaciones** e instalar en este orden:

1. **Argentina - Contabilidad** (`l10n_ar`) — localización argentina
2. **Argentinian Accounting UX** (`l10n_ar_ux`) — extensiones contables requeridas
3. **Argentina Importación Aduana** (`l10n_ar_importador_aduana`) — este módulo

> Si no aparecen, clic en **Actualizar lista de aplicaciones** primero.

Las cuentas **509.71 — derecho de importacion** y **509.55 — estadistica de importacion** se crean automáticamente al instalar el módulo.

---

## Configuration

### Paso 6 — Activar características completas de Contabilidad

Sin este paso el menú Contabilidad y el Plan de Cuentas no son visibles.

1. **Ajustes → Usuarios y Compañías → Grupos**
2. En el buscador escribir `contabilidad` → presionar **Búsqueda Grupo para: contabilidad**
3. Clic en **Técnico / Mostrar características de contabilidad completas**
4. Pestaña **Usuarios** → **Agregar línea** → seleccionar el usuario administrador
5. **Guardar**

### Paso 7 — Activar moneda USD

**Contabilidad → Configuración → Monedas**

Buscar **USD** y activarla con el toggle. Configurar la tasa de cambio del período.

### Paso 8 — Crear el proveedor "Aduana"

**Contabilidad → Proveedores → Proveedores → Crear**

Completar:
- **Nombre:** el nombre de la aduana o despachante (ej: `Aduana`)
- **Tipo:** Compañía
- **CUIT:** el CUIT correspondiente

Guardar.

### Paso 9 — Crear el diario de importaciones

**Contabilidad → Configuración → Diarios → Crear**

Completar:
- **Nombre del diario:** el nombre que uses en tu empresa (ej: `Importaciones`, `Aduana`, etc.)
- **Tipo:** Efectivo
- **Moneda:** dejar vacío (Odoo usa ARS por defecto; no seleccionar ARS explícitamente)

> Las cuentas contables del diario (efectivo, transitoria, ganancias, pérdidas) se asignan automáticamente por Odoo al seleccionar el tipo.

Guardar.

### Paso 10 — Configurar cuentas en Ajustes (opcional)

Las cuentas **509.71** y **509.55** se usan por defecto. Si tu empresa utiliza otras cuentas contables para registrar el Arancel y la Estadística:

1. **Ajustes → Facturación / Contabilidad → Aduana DUA**
2. Completar:
   - **Cuenta Gastos DUA (Arancel):** cuenta a usar para ARANCEL (código 010). Por defecto: `509.71`
   - **Cuenta Estadística / SIM:** cuenta para TASA ESTADÍSTICA (011/061) y SIM (500). Por defecto: `509.55`
3. **Guardar**

> Si los campos se dejan vacíos, el módulo usa automáticamente 509.71 y 509.55.

### Paso 11 — Impuestos IIBB provinciales

Los impuestos de Ingresos Brutos por jurisdicción provincial vienen incluidos con la localización argentina (`l10n_ar`). El módulo los detecta y aplica automáticamente según las jurisdicciones que figuren en cada despacho.

**Si aparece una nueva jurisdicción no configurada**, el wizard informará el error. En ese caso:

1. **Contabilidad → Configuración → Impuestos → Crear**
2. Completar:
   - **Nombre:** `IIBB [Provincia] – Percepción Importación`
   - **Código de jurisdicción:** el que figura en el PDF del despacho (ej: `904` para Córdoba)
   - **Tipo de impuesto:** Compras
   - **Cálculo:** Porcentaje sobre el precio
   - **Importe:** alícuota provincial según Convenio Multilateral
   - **Cuenta:** cuenta de percepción IIBB de tu plan de cuentas
3. Guardar y reprocesar el despacho

---

## Uso

1. **Contabilidad → Proveedores → Importar DUA desde PDF**
2. Subir el **PDF del despacho** (Formulario OM-1993)
3. Seleccionar el **Proveedor** (Aduana)
4. Seleccionar el **Diario** creado en el Paso 9
5. Clic en **Procesar**
6. Revisar la **Factura de Proveedor** generada en borrador
7. **Confirmar**

---

## Known Issues

- El módulo procesa despachos en formato **OM-1993 estándar**. PDFs escaneados o con formato modificado pueden no ser reconocidos correctamente.
- Si aparece una nueva jurisdicción IIBB no configurada, el wizard informa el error y requiere crear el impuesto correspondiente antes de reprocesar (ver Paso 11).
- En instalaciones multi-empresa, las cuentas 509.71 y 509.55 se asignan a la empresa principal. Para otras empresas, configurar en **Ajustes → Aduana DUA** dentro del contexto de cada empresa.

---

## Credits

**Author:** ArgenCode Tech  
**License:** OPL-1  
**Version:** 15.0.3.0.0

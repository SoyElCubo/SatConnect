import base64
import logging
from datetime import datetime
from io import BytesIO

from lxml import etree
from lxml.objectify import fromstring

from odoo import _, api, fields, models, tools
from odoo.tools.xml_utils import _check_with_xsd
from odoo.tools import DEFAULT_SERVER_TIME_FORMAT
from odoo.tools import float_round
from odoo.exceptions import UserError


CFDI_TEMPLATE_33 = 'data/cfdiv33.xml'
# Mapped from original SAT state to l10n_mx_edi_sat_status selection value
#https://consultaqr.facturaelectronica.sat.gob.mx/ConsultaCFDIService.svc?wsdl
CFDI_SAT_QR_STATE = {
    'No Encontrado': 'not_found',
    'Cancelado': 'cancelled',
    'Vigente': 'valid',
}

_logger = logging.getLogger(__name__)
def create_list_html(array):
    ''' COnvierte una matriz de  strings a una lista HTML.
    :param array: Una lista de strings
    :return: una cadena vacia si no es una matriz, en caso contrario una lista HTML.
    '''
    if not array:
        return ''
    msg = ''
    for item in array:
        msg += '<li>' + item + '</li>'
    return '<ul>' + msg + '</ul>'

class SatConnect(models.Model):
     _inherit = 'hr.payslip'


     fac_cfdi_name = fields.Char(string='CFDI name', copy=False, readonly=True,
                                 help='The attachment name of the CFDI.')

     fac_payment_method_id = fields.Many2one('fac.payment.method',
                                                    string='Payment Way',
                                                    readonly=True,
                                                    states={'draft': [('readonly', False)]},
                                                    help='Indicates the way the invoice was/will be paid, where the '
                                                         'options could be: Cash, Nominal Check, Credit Card, etc. Leave empty '
                                                         'if unkown and the XML will show "Unidentified".',
                                                    default=lambda self: self.env.ref(
                                                        'l10n_mx_edi.payment_method_otros',
                                                        raise_if_not_found=False))




     @api.model
     def fac_get_xml_etree(self, cfdi=None):
        '''Obtiene el arbol de objetos que representa al cfdi.
        Si el CFDI no esta especificado, recuperarlo del archivo adjunto.

        :param cfdi: EL cfdi es un string
        :return: un arbol de objetos
        '''
        self.ensure_one()
        if cfdi is None and self.fac_cfdi:
            cfdi = base64.decodestring(self.fac_cfdi)
        return fromstring(cfdi) if cfdi else None

     @api.model
     def fac_generate_cadena(self, xslt_path, cfdi_as_tree):
        '''Genera la cadena del  cfdi basado en el archivo xslt.
        The cadena is the sequence of data formed with the information contained within the cfdi.
        This can be enco palacio del oded with the certificate to create the digital seal.
        Since the cadena is generated with the invoice data, any change in it will be noticed resulting in a different
        cadena and so, ensure the invoice has not been modified.

        :param xslt_path: The path to the xslt file.
        :param cfdi_as_tree: The cfdi converted as a tree
        :return: A string computed with the invoice data called the cadena
        '''
        xslt_root = etree.parse(tools.file_open(xslt_path))
        return str(etree.XSLT(xslt_root)(cfdi_as_tree))

     @api.model
     def fac_get_pac_version(self):
        '''Dev uelve la ´versión cfdi´ para generar el CFDI.
        '''
        version = self.env['ir.config_parameter'].sudo().get_param(
            'fac_cfdi_version', '3.3')
        return version


     def fac_create_cfdi(self):

        '''Creates and returns a dictionnary containing 'cfdi' if the cfdi is well created, 'error' otherwise.
        '''
        self.ensure_one()
        qweb = self.env['ir.qweb']
        error_log = []
        company_id = self.company_id
        pac_name = company_id.l10n_mx_edi_pac
        values = self.to_json()

        # -----------------------
        # Check the configuration
        # -----------------------
        # -Check certificate
        certificate_ids = company_id.l10n_mx_edi_certificate_ids
        certificate_id = certificate_ids.sudo().get_valid_certificate()
        if not certificate_id:
            error_log.append(_('No valid certificate found'))

        # -Check PAC
        if pac_name:
            pac_test_env = company_id.l10n_mx_edi_pac_test_env
            pac_password = company_id.l10n_mx_edi_pac_password
            if not pac_test_env and not pac_password:
                error_log.append(_('No PAC credentials specified.'))
        else:
            error_log.append(_('No PAC specified.'))

        if error_log:
            return {'error': _('Please check your configuration: ') + create_list_html(error_log)}

        version = self.l10n_mx_edi_get_pac_version()

        # -Compute cfdi
        cfdi = qweb.render(CFDI_TEMPLATE_33, values=values)
        cfdi = cfdi.replace(b'xmlns__', b'xmlns:')
        node_sello = 'Sello'
        attachment = self.env.ref('l10n_mx_edi.xsd_cached_cfdv33_xsd', False)
        xsd_datas = base64.b64decode(attachment.datas) if attachment else b''

        # -Compute cadena
        tree = self.fac_get_xml_etree(cfdi)
        cadena = self.fac_set_data_from_xml(CFDI_TEMPLATE_33 % version, tree)

        # Check with xsd
        if xsd_datas:
            try:
                with BytesIO(xsd_datas) as xsd:
                    _check_with_xsd(tree, xsd)
            except (IOError, ValueError):
                _logger.info(_('The xsd file to validate the XML structure was not found'))
            except Exception as e:
                return {'error': (_('The cfdi generated is not valid') +
                                  create_list_html(str(e).split('\\n')))}

     def datukis(self):
         datos = self.request_params
         _logger.info(datos)
         return datos

     def fac_set_data_from_xml(self, xml_invoice):
         if not xml_invoice:
             return None
         NSMAP = {
             'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
             'cfdi': 'http://www.sat.gob.mx/cfd/3',
             'tfd': 'http://www.sat.gob.mx/TimbreFiscalDigital',
         }

         xml_data = etree.fromstring(xml_invoice)
         Emisor = xml_data.find('cfdi:Emisor', NSMAP)
         RegimenFiscal = Emisor.find('cfdi:RegimenFiscal', NSMAP)
         Complemento = xml_data.find('cfdi:Complemento', NSMAP)
         TimbreFiscalDigital = Complemento.find('tfd:TimbreFiscalDigital', NSMAP)

         self.rfc_emisor = Emisor.attrib['Rfc']
         self.name_emisor = Emisor.attrib['Nombre']
         self.tipocamx = TimbreFiscalDigital.attrib['SelloSAT']
         self.folio_fiscal = TimbreFiscalDigital.attrib['UUID']
         if self.number:
             self.folio = xml_data.attrib['Folio']
         if self.company_id.serie_nomina:
             self.serie_emisor = xml_data.attrib['Serie']
         self.invoice_datetime = xml_data.attrib['Fecha']
         self.version = TimbreFiscalDigital.attrib['Version']
         self.cadena_origenal = '||%s|%s|%s|%s|%s||' % (self.version, self.folio_fiscal, self.fecha_certificacion,
                                                        self.selo_digital_cdfi, self.cetificaso_sat)

         options = {'width': 275 * mm, 'height': 275 * mm}
         amount_str = str(self.total_nomina).split('.')
         # print 'amount_str, ', amount_str
         qr_value = 'https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx?&id=%s&re=%s&rr=%s&tt=%s.%s&fe=%s' % (
         self.folio_fiscal,
         self.company_id.rfc,
         self.employee_id.rfc,
         amount_str[0].zfill(10),
         amount_str[1].ljust(6, '0'),
         self.selo_digital_cdfi[-8:],
         )
         self.qr_value = qr_value
         ret_val = createBarcodeDrawing('QR', value=qr_value, **options)
         self.qrcode_image = base64.encodestring(ret_val.asString('jpg'))
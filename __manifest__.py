# -*- coding: utf-8 -*-
{
    'name': "Facturacion masiva",

    'summary': """
        Short (1 phrase/line) summary of the module's purpose, used as
        subtitle on modules listing or apps.openerp.com""",

    'description': """
        Long description of module's purpose
    """,

    'author': "Cesar O. B. J.",
    'website': "Bitcoin",


    # any module necessary for this one to work correctly
    'depends': ['base', 'nomina_cfdi_ee'],

    # always loaded
    'data': [
        # 'security/ir.model.access.csv',
        'views/sat_connect_views.xml',
    ],

}
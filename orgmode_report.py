# -*- coding: utf-8 -*-
##############################################################################
#
# Copyright (c) 2010 Moldeo Interactive Coop Trab. (http://moldeo.coop)
# All Right Reserved
#
# Author : Cristian S. Rocha (Moldeo Interactive)
#
# WARNING: This program as such is intended to be used by professional
# programmers who take the whole responsability of assessing all potential
# consequences resulting from its eventual inadequacies and bugs
# End users who are looking for a ready-to-use solution with commercial
# garantees and support are strongly adviced to contract a Free Software
# Service Company
#
# This program is Free Software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
##############################################################################

import subprocess
import os
import sys
from openerp import report
import tempfile
import time
import logging
import shutil

import pystache, pprint, imghdr, base64

from openerp import netsvc
from openerp import pooler
from openerp.report.report_sxw import *
from openerp import addons
from openerp import tools
from openerp.tools.translate import _
from openerp.osv import osv

_logger = logging.getLogger(__name__)


# States to parse log file
LOGNOMESSAGE = 0
LOGWAITLINE  = 1
LOGINLINE    = 2
LOGINHELP    = 3

# Text expected in log file to rerun
# Rerun check need <rerunfilecheck> package
RERUNTEXT = "Rerun to get outlines right"

class OrgmodeParser(report_sxw):
    """Custom class that use pystache, orgmode and emacs to render PDF reports
       Code partially taken from report Org-mode. Thanks guys :)
    """
    def __init__(self, name, table, rml=False, parser=False,
        header=True, store=False):
        self.parser_instance = False
        self.localcontext = {}
        report_sxw.__init__(self, name, table, rml, parser,
            header, store)

    def get_lib(self, cursor, uid):
        """Return the emacs path"""
        proxy = self.pool.get('ir.config_parameter')
        emacs_path = proxy.get_param(cursor, uid, 'emacs_path')

        if not emacs_path:
            try:
                defpath = os.environ.get('PATH', os.defpath).split(os.pathsep)
                if hasattr(sys, 'frozen'):
                    defpath.append(os.getcwd())
                    if tools.config['root_path']:
                        defpath.append(os.path.dirname(tools.config['root_path']))
                emacs_path = tools.which('emacs', path=os.pathsep.join(defpath))
            except IOError:
                emacs_path = None

        if emacs_path:
            return emacs_path

        raise osv.except_osv(
                         _('emacs executable path is not set'),
                         _('Please install executable on your system' \
                         ' (sudo apt-get install emacs24)')
                        )

    def generate_pdf(self, comm_path, report_xml, org, resource_path=None):
        """Call org-mode in order to generate pdf"""
        tmp_dir = tempfile.mkdtemp()
        if comm_path:
            command = [comm_path]
        else:
            command = ['emacs']

        count = 0

        #Execute block with latex clas into org file
        echo = subprocess.Popen(('/bin/echo', 'yes'), stdout=subprocess.PIPE)

        prefix_filename = str(time.time()) + str(count)
        org_filename = prefix_filename +'.org'
        pdf_filename = prefix_filename +'.pdf'
        log_filename = prefix_filename +'.log'
        org_file = file(os.path.join(tmp_dir, org_filename), 'w')
        count += 1
        org_file.write(org)
        org_file.close()
        command.append(tmp_dir+"/"+org_filename)
        command.extend(['--batch', '-f', 'org-babel-execute-src-block-maybe', '-f', 'org-export-as-pdf'])

        #Extract company_logo image
        company_logo = self.parser_instance.localcontext['logo']
        binimg = base64.b64decode(company_logo)
        suffix = imghdr.what('', binimg)
        img_filename = "company_logo"+"."+suffix
        img_file = file(os.path.join(tmp_dir, img_filename), 'w')
        img_file.write(binimg)
        img_file.close()

        env = os.environ
        if resource_path:
            env.update(dict(TEXINPUTS="%s:" % resource_path))

        _logger.debug("Environment Variables: %s" % env)

        stderr_fd, stderr_path = tempfile.mkstemp(dir=tmp_dir,text=True)
        try:
            _logger.info("Source Org-mode File: %s" % os.path.join(tmp_dir, org_filename))
            output = subprocess.check_output(command, stdin=echo.stdout, stderr=stderr_fd, env=env)

            os.close(stderr_fd) # ensure flush before reading
            stderr_fd = None # avoid closing again in finally block

            pdf_file = open(os.path.join(tmp_dir, pdf_filename), 'rb')
            pdf = pdf_file.read()
            pdf_file.close()
        except:
            latex_log = open(os.path.join(tmp_dir, log_filename), 'rb')
            log = latex_log.read()
            _logger.error(log)
            raise osv.except_osv(_('Org-mode document error'),
                  _("The command 'emacs' failed with error. Read logs."))
        finally:
            if stderr_fd is not None:
                os.close(stderr_fd)
            try:
                _logger.debug('Removing temporal directory: %s', tmp_dir)
                shutil.rmtree(tmp_dir)
            except (OSError, IOError), exc:
                _logger.error('Cannot remove dir %s: %s', tmp_dir, exc)
        return pdf

    def translate_call(self, src):
        """Translate String."""
        ir_translation = self.pool.get('ir.translation')
        name = self.tmpl and 'addons/' + self.tmpl or None
        res = ir_translation._get_source(self.parser_instance.cr, self.parser_instance.uid,
                                         name, 'report', self.parser_instance.localcontext.get('lang', 'en_US'), src)
        if res == src:
            # no translation defined, fallback on None (backward compatibility)
            res = ir_translation._get_source(self.parser_instance.cr, self.parser_instance.uid,
                                             None, 'report', self.parser_instance.localcontext.get('lang', 'en_US'), src)
        if not res :
            return src
        return res

    # override needed to keep the attachments storing procedure
    def create_single_pdf(self, cursor, uid, ids, data, report_xml, context=None):
        """generate the PDF"""
        if context is None:
            context={}
        if report_xml.report_type != 'orgmode':
            return super(OrgmodeParser,self).create_single_pdf(cursor, uid, ids, data, report_xml, context=context)

        self.parser_instance = self.parser(cursor,
                                           uid,
                                           self.name2,
                                           context=context)

        self.pool = pooler.get_pool(cursor.dbname)
        objs = self.getObjects(cursor, uid, ids, context)
        self.parser_instance.set_context(objs, data, ids, report_xml.report_type)

        template =  False
        resource_path = None

        if report_xml.report_file :
            # backward-compatible if path in Windows format
            report_path = report_xml.report_file.replace("\\", "/")
            path = addons.get_module_resource(*report_path.split('/'))
            if path and os.path.exists(path) :
                resource_path = os.path.dirname(path)
                template = file(path).read()
                template_utf8 = unicode(template, 'utf-8')
        if not template :
            raise osv.except_osv(_('Error!'), _('Org-mode report template not found!'))

        try :
            org = pystache.render(template_utf8, self.parser_instance.localcontext).encode('utf-8')
        except Exception:
            msg = "Error en archivo ORG"
            _logger.error(msg)
            raise osv.except_osv(_('Orgmode render!'), msg)

        finally:
            _logger.info("Removing temporal directory from helper.")
        bin = self.get_lib(cursor, uid)
        pdf = self.generate_pdf(bin, report_xml, org, resource_path=resource_path)
        return (pdf, 'pdf')


    def create(self, cursor, uid, ids, data, context=None):
        """We override the create function in order to handle generator
           Code taken from report openoffice. Thanks guys :) """
        pool = pooler.get_pool(cursor.dbname)
        ir_obj = pool.get('ir.actions.report.xml')
        report_xml_ids = ir_obj.search(cursor, uid,
                [('report_name', '=', self.name[7:])], context=context)
        if report_xml_ids:

            report_xml = ir_obj.browse(cursor,
                                       uid,
                                       report_xml_ids[0],
                                       context=context)
            report_xml.report_rml = None
            report_xml.report_rml_content = None
            report_xml.report_sxw_content_data = None
            report_xml.report_sxw_content = None
            report_xml.report_sxw = None
        else:
            return super(OrgmodeParser, self).create(cursor, uid, ids, data, context)
        if report_xml.report_type != 'orgmode' :
            return super(OrgmodeParser, self).create(cursor, uid, ids, data, context)
        result = self.create_source_pdf(cursor, uid, ids, data, report_xml, context)
        if not result:
            return (False,False)
        return result

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:

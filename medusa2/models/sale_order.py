#from odoo import api, fields, models, exceptions, _
#from odoo.exceptions import ValidationError, UserError
#from odoo.tools import float_is_zero, float_compare
#
#class SaleOrder(models.Model):
#    _inherit = "sale.order"
#
#    @api.multi
#    def action_confirm(self):
#        imediate_obj=self.env['stock.immediate.transfer']
#        res=super(SaleOrder,self).action_confirm()
#        for order in self:
#
#            warehouse=order.warehouse_id
#            if warehouse.is_delivery_set_to_done and order.picking_ids: 
#                for picking in self.picking_ids:
#                    picking.sudo().action_confirm()
#                    picking.sudo().action_assign()
#
#
#                    imediate_rec = imediate_obj.sudo().create({'pick_ids': [(4, order.picking_ids.id)]})
#                    imediate_rec.process()
#                    if picking.state !='done':
#                        for move in picking.move_ids_without_package:
#                            move.quantity_done = move.product_uom_qty
#                        picking.sudo().button_validate()
#
#            self._cr.commit()
#
#            if warehouse.create_invoice and not order.invoice_ids:
#                order.sudo().action_invoice_create()
#
#            if warehouse.validate_invoice and order.invoice_ids:
#                for invoice in order.invoice_ids:
#                    invoice.sudo().action_invoice_open()
#
#        return res
#
#    @api.multi
#    def _prepare_invoice(self):
#        """
#        Prepare the dict of values to create the new invoice for a sales order. This method may be
#        overridden to implement custom invoice generation (making sure to call super() to establish
 #       a clean extension chain).
#        """
#        self.ensure_one()
#        company_id = self.company_id.id
#        journal_id = (self.env['account.invoice'].with_context(company_id=company_id or self.env.user.company_id.id)
#            .default_get(['journal_id'])['journal_id'])
#        if not journal_id:
#            raise UserError(_('Please define an accounting sales journal for this company.'))
#
#        property_account_receivable_id = self.partner_invoice_id.property_account_receivable_id
#        if property_account_receivable_id.company_id != company_id:
#            account_id = self.env['account.account'].sudo().search([('code', '=', property_account_receivable_id.code), ('company_id', '=', company_id)])
#            if account_id:
#                property_account_receivable_id = account_id
#
#        return {
#            'name': (self.client_order_ref or '')[:2000],
#            'origin': self.name,
#            'type': 'out_invoice',
#            'account_id': property_account_receivable_id.id,
#            'partner_shipping_id': self.partner_shipping_id.id,
#            'journal_id': journal_id,
#            'currency_id': self.pricelist_id.currency_id.id,
#            'comment': self.note,
#            'partner_id': self.partner_invoice_id.id,
#            'payment_term_id': self.payment_term_id.id,
#            'fiscal_position_id': self.fiscal_position_id.id or self.partner_invoice_id.property_account_position_id.id,
#            'company_id': company_id,
#            'user_id': self.user_id and self.user_id.id,
#            'team_id': self.team_id.id,
#            'transaction_ids': [(6, 0, self.transaction_ids.ids)],
#            'payment_methods_id': self.payment_method_id.id or self.partner_id.payment_methods_id.id
#        }
from odoo import models, fields, api
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)

class StockWarningWizard(models.TransientModel):
    _name = 'stock.warning.wizard'
    _description = 'Stock Insufficient Warning Wizard'

    message = fields.Text(string="Advertencia", readonly=True)

    @api.multi
    def action_confirm(self):
        sale_order_id = self.env.context.get('sale_order_id')
        if sale_order_id:
            sale_order = self.env['sale.order'].browse(sale_order_id)
            _logger.warning(f"Pedido {sale_order.name} confirmado a pesar de advertencia de stock insuficiente.")
            sale_order.sudo().action_confirm()
        return {'type': 'ir.actions.act_window_close'}

class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.multi
    def action_confirm(self):
        stock_warnings = []

        for order in self:
            for picking in order.picking_ids:
                for move in picking.move_ids_without_package:
                    if move.reserved_availability < move.product_uom_qty:
                        stock_warnings.append({
                            'product': move.product_id.name,
                            'needed_qty': move.product_uom_qty,
                            'available_qty': move.reserved_availability,
                        })

        if stock_warnings:
            message = "\n".join([
                _("Producto: %s | Cantidad requerida: %s | Cantidad disponible: %s") % (
                    warning['product'], warning['needed_qty'], warning['available_qty']
                )
                for warning in stock_warnings
            ])
            full_message = _(
                "Algunos productos no tienen suficiente disponibilidad para completar el pedido:\n\n%s\n\n"
                "Si confirma el pedido en este estado, no se podrá hacer la salida de inventario y la facturación no será posible. "
                "¿Desea continuar con la confirmación?"
            ) % message
            # Llama al wizard para advertir al usuario y permitir que continúe si lo desea
            return {
                'name': _('Stock insuficiente'),
                'type': 'ir.actions.act_window',
                'res_model': 'stock.warning.wizard',
                'view_mode': 'form',
                'view_type': 'form',
                'target': 'new',
                'context': {
                    'default_message': full_message,
                    'sale_order_id': self.id
                }
            }

        # Si no hay advertencias de stock, proceder con la confirmación del pedido
        super(SaleOrder, self).action_confirm()

        # Actualizar qty_done en los movimientos de inventario después de confirmar el pedido
        for order in self:
            for picking in order.picking_ids:
                if picking.state in ['confirmed', 'assigned', 'waiting']:
                    picking.sudo().action_confirm()
                    picking.sudo().action_assign()

                    # Asignar automáticamente qty_done con la cantidad reservada
                    for move in picking.move_lines:
                        if move.reserved_availability > 0:
                            move.qty_done = move.reserved_availability

        return True



class PickingValidationWizard(models.TransientModel):
    _name = 'picking.validation.wizard'
    _description = 'Wizard para validar los pickings asociados a una factura'

    invoice_id = fields.Many2one('account.invoice', string="Factura", required=True)
    picking_ids = fields.Many2many('stock.picking', string="Pickings a validar", readonly=True)

    @api.model
    def default_get(self, fields):
        res = super(PickingValidationWizard, self).default_get(fields)
        invoice_id = self.env.context.get('default_invoice_id')
        invoice = self.env['account.invoice'].browse(invoice_id)
        
        if invoice.origin:
            sale_order = self.env['sale.order'].search([('name', '=', invoice.origin)], limit=1)
            if sale_order:
                res['picking_ids'] = [(6, 0, sale_order.picking_ids.ids)]
                res['invoice_id'] = invoice.id
        return res

    def action_validate_pickings(self):
        for picking in self.picking_ids:
            if picking.state not in ['done', 'cancel']:
                picking.sudo().action_confirm()
                picking.sudo().action_assign()

                # Asignar automáticamente la cantidad hecha (qty_done)
                for move_line in picking.move_line_ids:
                    move_line.qty_done = move_line.product_uom_qty  # Asignar la cantidad hecha

                # Forzar la validación del picking
                picking.sudo().button_validate()

        # Registrar en la factura que se validaron los movimientos
        self.invoice_id.message_post(body=_("Los movimientos de inventario relacionados al pedido han sido validados desde el wizard."))
        return {'type': 'ir.actions.act_window_close'}



class AccountInvoice(models.Model):
    _inherit = "account.invoice"

    # Campo Many2one que relaciona la factura con el pedido de venta
    sale_order_id = fields.Many2one('sale.order', string="Pedido de Venta Relacionado", readonly=True)

    @api.onchange('origin')
    def _onchange_origin(self):
        # Automáticamente rellenar el campo sale_order_id si hay un pedido relacionado
        if self.origin:
            sale_order = self.env['sale.order'].search([('name', '=', self.origin)], limit=1)
            if sale_order:
                self.sale_order_id = sale_order

    @api.multi
    def action_invoice_open(self):
        # Llamar al método original para validar la factura
        res = super(AccountInvoice, self).action_invoice_open()

        for invoice in self:
            if invoice.sale_order_id:
                # Obtener los pickings relacionados al pedido de venta
                sale_order = invoice.sale_order_id
                for picking in sale_order.picking_ids:
                    if picking.state not in ['done', 'cancel']:
                        picking.sudo().action_confirm()
                        picking.sudo().action_assign()

                        # Asignar automáticamente la cantidad hecha (qty_done)
                        for move_line in picking.move_line_ids:
                            move_line.qty_done = move_line.product_uom_qty  # Asignar la cantidad hecha igual a la reservada

                        # Validar el picking forzando la validación
                        picking.sudo().button_validate()

                # Registrar en la factura que los movimientos de inventario han sido validados
                invoice.message_post(body=_("Los movimientos de inventario relacionados al pedido %s han sido confirmados y procesados.") % sale_order.name)

        return res

    @api.multi
    def action_credit_note_create(self):
        for invoice in self:
            if invoice.invoice_id:  # Asegura que estamos trabajando con una nota de crédito
                original_invoice = invoice.invoice_id  # Factura original relacionada
                sale_order = original_invoice.sale_order_id  # Usar el campo relacionado del pedido de venta

                if not sale_order:
                    raise UserError(_("No se encontró el pedido de venta relacionado con la factura original."))

                for line in invoice.invoice_line_ids:
                    product = line.product_id
                    if product.type != 'service':
                        # Verificar que el producto haya salido del inventario
                        related_moves = sale_order.mapped('picking_ids').mapped('move_lines').filtered(
                            lambda move: move.product_id == product and move.state == 'done')
                        
                        if not related_moves:
                            raise UserError(_(
                                "El producto %s no ha salido del inventario en la factura original. La nota de crédito no procede."
                            ) % product.name)
                        
                        # Crear movimiento de devolución por los productos devueltos
                        picking = sale_order.picking_ids.filtered(lambda p: p.state == 'done')
                        if picking:
                            return_wizard = self.env['stock.return.picking'].create({'picking_id': picking.id})
                            return_wizard.product_return_moves.filtered(lambda r: r.product_id == product).update({
                                'quantity': line.quantity
                            })
                            return_wizard.create_returns()
                            _logger.info(
                                "Se ha creado una devolución para el producto %s asociado a la factura %s",
                                product.name, original_invoice.number
                            )
                        else:
                            raise UserError(_("No se encontró un picking válido para realizar la devolución."))
                        
                invoice.message_post(body=_("Se ha creado una devolución relacionada con la nota de crédito para el pedido de venta: %s" % sale_order.name))

        return super(AccountInvoice, self).action_credit_note_create()
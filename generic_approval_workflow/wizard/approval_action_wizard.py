# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ApprovalActionWizard(models.TransientModel):
    _name = 'approval.action.wizard'
    _description = 'Approval Action Comment'

    res_model = fields.Char(string='Model', required=True)
    res_id = fields.Integer(string='Record ID', required=True)
    action_type = fields.Selection(
        selection=[
            ('approve', 'Approve'),
            ('reject', 'Reject'),
            ('send_back', 'Send Back'),
            ('cancel', 'Cancel'),
        ],
        string='Action', required=True)
    comment = fields.Text(string='Comment')
    comment_required = fields.Boolean(
        string='Comment Required', compute='_compute_comment_required')

    @api.depends('action_type')
    def _compute_comment_required(self):
        for wizard in self:
            wizard.comment_required = wizard.action_type in ('reject', 'send_back')

    def action_confirm(self):
        self.ensure_one()
        if self.comment_required and not self.comment:
            raise UserError(_('Please give a reason.'))
        record = self.env[self.res_model].browse(self.res_id)
        approval = self.env['approval.workflow.record']._get_or_create(record)
        if not approval:
            raise UserError(_('No approval workflow matches this record.'))
        if self.action_type == 'approve':
            approval.action_approve(comment=self.comment)
        elif self.action_type == 'reject':
            approval.action_reject(comment=self.comment)
        elif self.action_type == 'send_back':
            approval.action_send_back(comment=self.comment)
        elif self.action_type == 'cancel':
            approval.action_cancel(comment=self.comment)
        return {'type': 'ir.actions.act_window_close'}

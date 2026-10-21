# -*- coding: utf-8 -*-

from odoo import fields, models


class ApprovalHistory(models.Model):
    _name = 'approval.history'
    _description = 'Approval History'
    _order = 'create_date desc, id desc'
    _rec_name = 'display_name'

    workflow_id = fields.Many2one('approval.workflow', string='Workflow', ondelete='set null')
    stage_id = fields.Many2one('approval.workflow.stage', string='Approval Stage',
                               ondelete='set null')
    stage_name = fields.Char(string='Stage')
    res_model = fields.Char(string='Model', required=True, index=True)
    res_model_id = fields.Many2one('ir.model', string='Model Name', ondelete='cascade')
    res_id = fields.Integer(string='Record ID', required=True, index=True)
    res_name = fields.Char(string='Record')
    action = fields.Selection(
        selection=[
            ('submit', 'Submitted'),
            ('approve', 'Approved'),
            ('reject', 'Rejected'),
            ('send_back', 'Sent Back'),
            ('cancel', 'Cancelled'),
            ('reset_draft', 'Reset to Draft'),
            ('next_stage', 'Moved to Next Stage'),
        ],
        string='Action', required=True)
    user_id = fields.Many2one(
        'res.users', string='Done by', required=True,
        default=lambda self: self.env.user)
    action_date = fields.Datetime(
        string='Date', required=True, default=fields.Datetime.now)
    previous_status = fields.Char(string='Previous Status')
    new_status = fields.Char(string='New Status')
    comment = fields.Text(string='Comment')
    cycle = fields.Integer(
        string='Cycle', default=1,
        help='Approval round. Resetting to draft starts a new round.')
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company)

    def _compute_display_name(self):
        for history in self:
            history.display_name = '%s - %s' % (
                history.res_name or history.res_model or '',
                dict(self._fields['action'].selection).get(history.action, ''),
            )

    def action_open_record(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
        }

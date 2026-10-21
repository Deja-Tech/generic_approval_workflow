# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ApprovalMixin(models.AbstractModel):
    """Add a configurable approval workflow to any model.

    Inherit it on the target model::

        class MyModel(models.Model):
            _name = 'my.model'
            _inherit = ['my.model', 'approval.mixin']
    """
    _name = 'approval.mixin'
    _description = 'Approval Workflow Mixin'

    approval_workflow_id = fields.Many2one(
        'approval.workflow', string='Approval Workflow', copy=False, readonly=True)
    approval_stage_id = fields.Many2one(
        'approval.workflow.stage', string='Approval Stage', copy=False,
        readonly=True, tracking=True)
    approval_state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('pending', 'Pending Approval'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('cancel', 'Cancelled'),
        ],
        string='Approval State', default='draft', copy=False,
        readonly=True, tracking=True)
    approval_status_label = fields.Char(
        string='Approval Status', compute='_compute_approval_status_label',
        help='Dynamic status: the name of the stage the record is waiting on.')
    approval_history_ids = fields.Many2many(
        'approval.history', string='Approval History',
        compute='_compute_approval_history', readonly=True)
    approval_history_count = fields.Integer(
        string='History Entries', compute='_compute_approval_history')
    approval_user_ids = fields.Many2many(
        'res.users', string='Current Approvers',
        compute='_compute_approval_user_ids')
    can_approve = fields.Boolean(
        string='Can Approve', compute='_compute_can_approve',
        help='The current user is an approver of the current stage.')
    approval_progress = fields.Char(
        string='Approvals', compute='_compute_can_approve')

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('approval_state', 'approval_stage_id')
    def _compute_approval_status_label(self):
        for record in self:
            if record.approval_state == 'pending' and record.approval_stage_id:
                record.approval_status_label = _('Pending %s', record.approval_stage_id.name)
            else:
                record.approval_status_label = dict(
                    self._fields['approval_state'].selection
                ).get(record.approval_state, '')

    @api.depends('approval_state', 'approval_stage_id')
    def _compute_approval_history(self):
        History = self.env['approval.history']
        for record in self:
            history = History.search([
                ('res_model', '=', record._name), ('res_id', '=', record.id)])
            record.approval_history_ids = history
            record.approval_history_count = len(history)

    @api.depends('approval_stage_id')
    def _compute_approval_user_ids(self):
        for record in self:
            stage = record.approval_stage_id
            record.approval_user_ids = stage._get_approver_users(record) if stage else False

    @api.depends('approval_stage_id', 'approval_state')
    def _compute_can_approve(self):
        for record in self:
            stage = record.approval_stage_id
            if record.approval_state != 'pending' or not stage:
                record.can_approve = False
                record.approval_progress = ''
                continue
            approvers = stage._get_approver_users(record)
            done = record._get_stage_approvals()
            record.can_approve = (
                self.env.user in approvers and self.env.user not in done.mapped('user_id'))
            record.approval_progress = '%s / %s' % (
                len(done), stage._get_required_approvals(record))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_stage_approvals(self):
        """Approvals already given on the current stage of this record."""
        self.ensure_one()
        if not self.approval_stage_id:
            return self.env['approval.history']
        return self.env['approval.history'].search([
            ('res_model', '=', self._name),
            ('res_id', '=', self.id),
            ('stage_id', '=', self.approval_stage_id.id),
            ('action', '=', 'approve'),
        ])

    def _log_approval(self, action, previous_status=None, new_status=None, comment=False,
                      stage=None):
        self.ensure_one()
        stage = stage if stage is not None else self.approval_stage_id
        return self.env['approval.history'].sudo().create({
            'workflow_id': self.approval_workflow_id.id,
            'stage_id': stage.id if stage else False,
            'stage_name': stage.name if stage else False,
            'res_model': self._name,
            'res_model_id': self.env['ir.model']._get_id(self._name),
            'res_id': self.id,
            'res_name': self.display_name,
            'action': action,
            'user_id': self.env.user.id,
            'previous_status': previous_status,
            'new_status': new_status,
            'comment': comment,
        })

    def _get_next_stage(self, stage):
        """Next applicable stage after the given one."""
        self.ensure_one()
        if stage.next_stage_id:
            candidates = stage.next_stage_id
        else:
            candidates = self.approval_workflow_id.stage_ids.filtered(
                lambda s: (s.sequence, s.id) > (stage.sequence, stage.id))
        for candidate in candidates.sorted(lambda s: (s.sequence, s.id)):
            if candidate._match_record(self):
                return candidate
        return self.env['approval.workflow.stage']

    def _get_previous_stage(self, stage):
        self.ensure_one()
        previous = self.approval_workflow_id.stage_ids.filtered(
            lambda s: (s.sequence, s.id) < (stage.sequence, stage.id))
        return previous.sorted(lambda s: (s.sequence, s.id))[-1:] if previous else previous

    def _notify_stage(self, stage, template_field='mail_template_id'):
        """Send the configured notification of a stage."""
        self.ensure_one()
        if not stage:
            return
        template = stage[template_field]
        partners = stage._get_notification_partners(self)
        if template:
            template.sudo().send_mail(
                self.id, force_send=True,
                email_values={'recipient_ids': [(6, 0, partners.ids)]} if partners else None,
                email_layout_xmlid='mail.mail_notification_light')
        elif partners and hasattr(self, 'message_post'):
            self.message_post(
                body=_('Approval required for stage %s.', stage.name),
                partner_ids=partners.ids,
                subtype_xmlid='mail.mt_comment')

    def _check_approval_rights(self):
        self.ensure_one()
        if not self.can_approve:
            raise UserError(
                _('You are not an approver of the stage "%s", or you already approved it.',
                  self.approval_stage_id.name or ''))

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------
    def action_submit_for_approval(self):
        """Start the workflow matching this record."""
        for record in self:
            if record.approval_state == 'pending':
                raise UserError(_('This record is already under approval.'))
            workflow = self.env['approval.workflow']._get_workflow_for_record(record)
            if not workflow:
                raise UserError(
                    _('No approval workflow matches this record. '
                      'Configure one under Approvals > Configuration.'))
            stages = workflow.stage_ids.sorted(lambda s: (s.sequence, s.id))
            stage = next((s for s in stages if s._match_record(record)), False)
            previous = record.approval_status_label
            if not stage:
                record.write({'approval_workflow_id': workflow.id,
                              'approval_state': 'approved'})
                record._log_approval('approve', previous, record.approval_status_label)
                continue
            record.write({
                'approval_workflow_id': workflow.id,
                'approval_stage_id': stage.id,
                'approval_state': 'pending',
            })
            record._log_approval('submit', previous, record.approval_status_label, stage=stage)
            record._notify_stage(stage)
        return True

    def action_approve(self, comment=False):
        for record in self:
            record._check_approval_rights()
            stage = record.approval_stage_id
            previous = record.approval_status_label
            record._log_approval('approve', previous, previous, comment=comment)
            approvals = len(record._get_stage_approvals())
            if approvals < stage._get_required_approvals(record):
                # still waiting for the other approvers of this stage
                record._notify_stage(stage, 'approve_template_id')
                continue
            next_stage = record._get_next_stage(stage)
            if next_stage:
                record.write({'approval_stage_id': next_stage.id,
                              'approval_state': 'pending'})
                record._log_approval('next_stage', previous, record.approval_status_label,
                                     stage=next_stage)
                record._notify_stage(next_stage)
            else:
                record.write({'approval_state': 'approved'})
                record._log_approval('approve', previous, record.approval_status_label,
                                     stage=stage)
                record._notify_stage(stage, 'approve_template_id')
                record._on_approval_done()
        return True

    def action_reject(self, comment=False):
        for record in self:
            record._check_approval_rights()
            stage = record.approval_stage_id
            previous = record.approval_status_label
            behavior = stage.reject_behavior
            if behavior == 'draft':
                record.write({'approval_state': 'draft', 'approval_stage_id': False})
            elif behavior == 'previous':
                target = record._get_previous_stage(stage)
                record.write({'approval_stage_id': target.id or False,
                              'approval_state': 'pending' if target else 'draft'})
            elif behavior == 'stage' and stage.reject_stage_id:
                record.write({'approval_stage_id': stage.reject_stage_id.id,
                              'approval_state': 'pending'})
            else:
                record.write({'approval_state': 'rejected'})
            record._log_approval('reject', previous, record.approval_status_label,
                                 comment=comment, stage=stage)
            record._notify_stage(stage, 'reject_template_id')
            record._on_approval_rejected()
        return True

    def action_send_back(self, comment=False):
        for record in self:
            record._check_approval_rights()
            stage = record.approval_stage_id
            previous = record.approval_status_label
            behavior = stage.return_behavior
            if behavior == 'draft':
                record.write({'approval_state': 'draft', 'approval_stage_id': False})
            elif behavior == 'stage' and stage.return_stage_id:
                record.write({'approval_stage_id': stage.return_stage_id.id,
                              'approval_state': 'pending'})
            else:
                target = record._get_previous_stage(stage)
                record.write({'approval_stage_id': target.id or False,
                              'approval_state': 'pending' if target else 'draft'})
            record._log_approval('send_back', previous, record.approval_status_label,
                                 comment=comment, stage=stage)
            record._notify_stage(stage, 'return_template_id')
        return True

    def action_approval_cancel(self, comment=False):
        for record in self:
            previous = record.approval_status_label
            record.write({'approval_state': 'cancel'})
            record._log_approval('cancel', previous, record.approval_status_label,
                                 comment=comment)
        return True

    def action_approval_reset_draft(self):
        for record in self:
            previous = record.approval_status_label
            record.write({'approval_state': 'draft', 'approval_stage_id': False})
            record._log_approval('reset_draft', previous, record.approval_status_label)
        return True

    # ------------------------------------------------------------------
    # Hooks and buttons
    # ------------------------------------------------------------------
    def _on_approval_done(self):
        """Called once the last stage is approved. Override on the target model."""
        return True

    def _on_approval_rejected(self):
        """Called when the record is rejected. Override on the target model."""
        return True

    def _open_approval_wizard(self, action):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Approval Comment'),
            'res_model': 'approval.action.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
                'default_action_type': action,
            },
        }

    def action_open_reject_wizard(self):
        return self._open_approval_wizard('reject')

    def action_open_send_back_wizard(self):
        return self._open_approval_wizard('send_back')

    def action_view_approval_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Approval History'),
            'res_model': 'approval.history',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
        }

# -*- coding: utf-8 -*-

from odoo import api, fields, models


class ApprovalModelState(models.Model):
    """Selectable states of a target model, so stages can be tied to them."""
    _name = 'approval.model.state'
    _description = 'Approval Target Model State'
    _order = 'model_name, sequence, id'
    _rec_name = 'display_name'

    value = fields.Char(string='Technical Value', required=True)
    label = fields.Char(string='State', required=True)
    model_id = fields.Many2one('ir.model', string='Model', required=True, ondelete='cascade')
    model_name = fields.Char(related='model_id.model', string='Model Name', store=True)
    field_name = fields.Char(string='Field', default='state', required=True)
    sequence = fields.Integer(string='Sequence', default=10)

    _value_uniq = models.Constraint(
        'unique(model_id, field_name, value)',
        'This state already exists for that model.')

    def _compute_display_name(self):
        for state in self:
            state.display_name = '%s (%s)' % (state.label, state.value)

    @api.model
    def _sync_model_states(self, model_name, field_name='state'):
        """Create the state records of a model from its selection field."""
        if not model_name or model_name not in self.env:
            return self.browse()
        Model = self.env[model_name]
        field = Model._fields.get(field_name)
        if not field or field.type != 'selection':
            return self.browse()
        try:
            selection = field._description_selection(self.env)
        except Exception:
            selection = field.selection if isinstance(field.selection, list) else []
        model_id = self.env['ir.model']._get_id(model_name)
        existing = self.sudo().search([
            ('model_id', '=', model_id), ('field_name', '=', field_name)])
        by_value = {state.value: state for state in existing}
        for index, (value, label) in enumerate(selection or []):
            values = {'label': label, 'sequence': index * 10}
            if value in by_value:
                by_value[value].sudo().write(values)
            else:
                self.sudo().create(dict(
                    values, value=value, model_id=model_id, field_name=field_name))
        return self.sudo().search([
            ('model_id', '=', model_id), ('field_name', '=', field_name)])

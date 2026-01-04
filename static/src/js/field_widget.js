/**@odoo-module */
import { registry } from "@web/core/registry"
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";
export class BoolBadge extends Component{
    updateValue(val){
        this.props.record.update({[this.props.name]: val })
    }
    get value() {
        return this.props.record.data[this.props.name]
    }
}
BoolBadge.template = "BoolBadge"
BoolBadge.props = {
        ...standardFieldProps,
        options: { type:Object, optional: true}
        }
export const boolBadge = {
    component: BoolBadge,
    supportedTypes: ["boolean"],
    extractProps: ({attrs}) =>{
        return {options: attrs.options}
    }
};
registry.category("fields").add("bool_badge", boolBadge)

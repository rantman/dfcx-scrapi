"""util class for doing searches"""

# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import time
from typing import Dict, List
from google.cloud.dialogflowcx_v3beta1 import types
from google.oauth2 import service_account
import pandas as pd
import numpy as np
from operator import attrgetter
from dfcx_scrapi.core import scrapi_base
from dfcx_scrapi.core import intents
from dfcx_scrapi.core import flows
from dfcx_scrapi.core import pages
from dfcx_scrapi.core import entity_types
from dfcx_scrapi.core import transition_route_groups


# logging config
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class SearchUtil(scrapi_base.ScrapiBase):
    """class for searching items"""

    def __init__(
        self,
        creds_path: str = None,
        creds_dict: Dict[str,str] = None,
        creds: service_account.Credentials = None,
        scope=False, #TODO type hint
        agent_id: str = None,
    ):
        super().__init__(
            creds_path=creds_path,
            creds_dict=creds_dict,
            creds=creds,
            scope=scope,
        )

        logging.info("create dfcx creds %s", creds_path)
        self.intents = intents.Intents(creds_path=creds_path, creds_dict=creds_dict)
        self.entities = entity_types.EntityTypes(
            creds_path=creds_path, creds_dict=creds_dict
        )
        self.flows = flows.Flows(creds_path=creds_path, creds_dict=creds_dict)
        self.pages = pages.Pages(creds_path=creds_path, creds_dict=creds_dict)
        self.route_groups = transition_route_groups.TransitionRouteGroups(
            creds_path, creds_dict
        )
        self.creds_path = creds_path
        self.intents_map = None
        if agent_id:
            self.agent_id = agent_id
            self.flow_map = self.flows.get_flows_map(
                agent_id=agent_id, reverse=True
            )
            self.intents_map = self.intents.get_intents_map(agent_id)
            self.client_options = self._set_region(agent_id)

    def _find_true_routes_flow_level(self, flow_display_name, flow_map):
        flow_id = flow_map[flow_display_name]
        start_page = self.flows.get_flow(flow_id) # pylint: disable=W0612
        other_pages = self.pages.list_pages(flow_id)

        # Start page - no entry fulfillment
        pages_dataframe = pd.DataFrame()
        for page in other_pages:
            display_name = page.display_name

            webhook = False
            if page.entry_fulfillment.webhook:
                webhook = True

            has_parameters = False
            if page.form.parameters:
                has_parameters = True

            has_true_route = False
            has_true_final_route = False
            for route in page.transition_routes:
                if route.condition == "true":
                    has_true_route = True

                if route.condition == '$page.params.status = "FINAL" AND true':
                    has_true_final_route = True

            page_dataframe = pd.DataFrame(
                columns=[
                    "flow_display_name",
                    "page_display_name",
                    "webhook_entry_fullfillment",
                    "has_parameters",
                    "has_true_route",
                    "has_true_and_final_route",
                ],
                data=[
                    [
                        flow_display_name,
                        display_name,
                        webhook,
                        has_parameters,
                        has_true_route,
                        has_true_final_route,
                    ]
                ],
            )
            pages_dataframe = pages_dataframe.append(page_dataframe)

        return pages_dataframe

    # Flows - event handlers
    def _flow_level_handlers(self):
        flows_in_agent = self.flows.list_flows(self.agent_id)

        flow_event_handler_data = pd.DataFrame()
        for flow in flows_in_agent:
            flow_level_event_handlers = flow.event_handlers
            flow_level_event_handlers_dataframe = pd.DataFrame()

            for handler in flow_level_event_handlers:
                flow_level_event_handlers_dataframe = (
                    flow_level_event_handlers_dataframe.append(
                        pd.DataFrame(
                            columns=[
                                "flow",
                                "event",
                                "messages",
                                "transition_flow",
                                "transition_page",
                            ],
                            data=[
                                [
                                    flow.display_name,
                                    handler.event,
                                    handler.trigger_fulfillment.messages,
                                    handler.target_flow,
                                    handler.target_page,
                                ]
                            ],
                        )
                    )
                )
                flow_event_handler_data = flow_event_handler_data.append(
                    flow_level_event_handlers_dataframe
                )

        return flow_event_handler_data

    # Pages - event handlers
    def _page_level_handlers(self):
        page_level_event_handlers_all_dataframe = pd.DataFrame()
        flow_map = self.flows.get_flows_map(self.agent_id)
        for flow_ in flow_map.keys():
            pages_in_flow = self.pages.list_pages(flow_)

            for page in pages_in_flow:
                page_level_event_handlers = page.event_handlers
                page_level_event_handlers_dataframe = pd.DataFrame()
                for handler in page_level_event_handlers:
                    page_level_event_handlers_dataframe = (
                        page_level_event_handlers_dataframe.append(
                            pd.DataFrame(
                                columns=[
                                    "flow",
                                    "page",
                                    "event",
                                    "messages",
                                    "transition_flow",
                                    "transition_page",
                                ],
                                data=[
                                    [
                                        flow_map[flow_],
                                        page.display_name,
                                        handler.event,
                                        handler.trigger_fulfillment.messages,
                                        handler.target_flow,
                                        handler.target_page,
                                    ]
                                ],
                            )
                        )
                    )

                page_level_event_handlers_all_dataframe = (
                    page_level_event_handlers_all_dataframe.append(
                        page_level_event_handlers_dataframe
                    )
                )
        return page_level_event_handlers_all_dataframe

    # Parameters - event handlers
    def _parameter_level_handlers(self):
        parameter_level_event_handlers_all_dataframe = pd.DataFrame()
        flow_map = self.flows.get_flows_map(self.agent_id)
        for flow_ in flow_map.keys():
            pages_in_flow = self.pages.list_pages(flow_)
            for page in pages_in_flow:
                parameters = page.form.parameters
                for parameter in parameters:
                    parameter_event_handlers = (
                        parameter.fill_behavior.reprompt_event_handlers
                    )
                    param_lvl_event_df = pd.DataFrame()
                    for handler in parameter_event_handlers:
                        param_lvl_event_df = param_lvl_event_df.append(
                            pd.DataFrame(
                                columns=[
                                    "flow",
                                    "page",
                                    "parameter",
                                    "event",
                                    "messages",
                                    "transition_flow",
                                    "transition_page",
                                ],
                                data=[
                                    [
                                        flow_map[flow_],
                                        page.display_name,
                                        parameter.display_name,
                                        handler.event,
                                        handler.trigger_fulfillment.messages,
                                        handler.target_flow,
                                        handler.target_page,
                                    ]
                                ],
                            )
                        )
                    parameter_level_event_handlers_all_dataframe = (
                        parameter_level_event_handlers_all_dataframe.append(
                            param_lvl_event_df
                        )
                    )
        return parameter_level_event_handlers_all_dataframe

    def find_list_parameters(self, agent_id):
        """This method extracts Parameters set at a page level that are
        designated as "lists".

        Page level parameters are tied to Entity Types and can be returned
        as String or List types. If the user selects "list" at the page
        level, the Entity Type will be returned with "is_list: True". This
        function will allow the user to provide an Agent ID and will return
        all instances of parameters being used as lists on pages.

        Args:
          - agent_id, the Agent ID string in the following format:
            projects/<project_id>/locations/<location_id>/agents/<agent_id>

        Returns:
          - params_map, a Dict of parameter names and Pages they belong to
        """

        # entities = self.dfcx.list_entity_types(agent_id)
        flows_map = self.flows.get_flows_map(agent_id)

        params_list = []

        for flow in flows_map.keys():
            temp_pages = self.pages.list_pages(flow)
            for page in temp_pages:
                for param in page.form.parameters:
                    if param.is_list:
                        params_list.append(param.display_name)

        return params_list

    def search_conditionals_page(self, page_id, search):
        """search page for an exact string in conditional routes

        Args:
          - page_id, the formatted CX Page ID to use
          - search, string to search

        Returns:
          - locator, dataframe of the results of where this string was found
        """

        locator = pd.DataFrame()
        page = self.pages.get_page(page_id=page_id)
        i = 1
        for route in page.transition_routes:
            if search.lower() in route.condition.lower():
                iter_frame = pd.DataFrame(
                    columns=["resource_id", "condition", "route_id"],
                    data=[[page_id, route.condition, i]],
                )
                locator = locator.append(iter_frame)
            i += 1

        return locator

    def search_conditionals_flow(self, flow_id, search):
        """search flow for an exact string in conditional routes

        Args:
        - flow_id, the formatted CX Flow ID to use
        - search, string to search

        Returns:
        - locator, dataframe of the results of where this string was found
        """

        locator = pd.DataFrame()
        flow = self.flows.get_flow(flow_id=flow_id)
        i = 1
        for route in flow.transition_routes:
            if search.lower() in route.condition.lower():
                iter_frame = pd.DataFrame(
                    columns=["resource_id", "condition", "route_id"],
                    data=[[flow_id, route.condition, i]],
                )
                locator = locator.append(iter_frame)
            i += 1

        return locator

    def search_conditionals(
        self,
        search,
        agent_id,
        flow_name=None,
        page_name=None,
        flag_search_all=False,
    ):
        """This is the master function where a user can search across
        all pages in a flow, an entire agent etc.
        Search conditionals for an exact string in conditional routes.

        Args:
            - search, string to search
            - agent_id, the formatted CX Agent ID to use
            - flow_name, (optional) the display name of the flow to search
            - page_name,  (optional) the display name of the page to search
            - flag_search_all, (optional)
                When set to True:
                    -if just an agent_id then entire agent is searched
                    -if just an agent_id and flow_name are specified
                        an entire flow is searched
                    -if an agent_id, flow_name and page_name are specified
                        a page is searched
                When set to False:
                    -if just an agent_id and flow_name are specified
                        only the start page of the flow is searched
                    -if an agent_id, flow_name and page_name are specified
                        a page is searched
        Returns:
            - locator, dataframe of the results of where this string was found
        """

        if page_name:
            try:
                flows_map = self.flows.get_flows_map(
                    agent_id=agent_id, reverse=True
                )
            # check - maybe other error types here
            except ValueError:
                logging.error(
                    "%s is not a valid flow_name for agent %s",
                    flow_name,
                    agent_id,
                )
            try:
                pages_map = self.pages.get_pages_map(
                    flow_id=flows_map[flow_name], reverse=True
                )
                return self.search_conditionals_page(
                    page_id=pages_map[page_name], search=search
                )

            except ValueError:
                logging.error(
                    "%s is not a valid page_name for flow %s in agent %s",
                    page_name,
                    flow_name,
                    agent_id,
                )

        if flow_name:
            locator = pd.DataFrame()
            try:
                flows_map = self.flows.get_flows_map(
                    agent_id=agent_id, reverse=True
                )
                flow_search = self.search_conditionals_flow(
                    flow_id=flows_map[flow_name], search=search
                )
                flow_search.insert(0, "resource_name", flow_name)
                flow_search.insert(0, "resource_type", "flow")
                locator = locator.append(flow_search)
            except ValueError:
                logging.error(
                    "%s is not a valid flow_name for agent %s",
                    flow_name,
                    agent_id,
                )

            if flag_search_all:

                pages_map = self.pages.get_pages_map(
                    flow_id=flows_map[flow_name], reverse=True
                )
                for page in pages_map:
                    page_search = self.search_conditionals_page(
                        page_id=pages_map[page], search=search
                    )
                    time.sleep(0.5)
                    page_search.insert(0, "resource_name", page)
                    page_search.insert(0, "resource_type", "page")
                    locator = locator.append(page_search)

            return locator

        if flow_name is None and page_name is None and flag_search_all is True:
            locator = pd.DataFrame()

            flows_map = self.flows.get_flows_map(
                agent_id=agent_id, reverse=True
            )
            for flow in flows_map:
                flow_search = self.search_conditionals_flow(
                    flow_id=flows_map[flow], search=search
                )
                flow_search.insert(0, "resource_name", flow)
                flow_search.insert(0, "resource_type", "flow")
                locator = locator.append(flow_search)
                pages_map = self.pages.get_pages_map(
                    flow_id=flows_map[flow], reverse=True
                )
                for page in pages_map:
                    page_search = self.search_conditionals_page(
                        page_id=pages_map[page], search=search
                    )
                    time.sleep(0.5)
                    page_search.insert(0, "resource_name", page)
                    page_search.insert(0, "resource_type", "page")
                    locator = locator.append(page_search)
            return locator

        # not found
        return None

    def find_true_routes(self, agent_id: str = None):
        """This method extracts data to see if routes with no parameters have a
        true route or pages with parameters have a true route +
        page.params.status = "Final" route. Having these routes ensure a user
        can escape this page no matter what.

        Args:
          - agent_id: The properly formatted CX Agent ID

        Returns:
          - agent_results: dataframe with:
              flow_display_name: display name of the associated page
              page_display_name: display name of the page with the associated
                data
              webhook_entry_fulfillments: True if a page has a webhook on entry
                else False
              has_parameters: True if a page has parameters else False
              has_true_route: True if a page has a true route else False
              has_true_and_final_route: True if a page has a route with true
                + page.params.status=Final else False
        """

        if not agent_id:
            agent_id = self.agent_id

        agent_results = pd.DataFrame()
        flow_map = self.flows.get_flows_map(
                agent_id=agent_id, reverse=True
            )

        for flow_display_name in flow_map.keys():
            flow_scan = self._find_true_routes_flow_level(
                flow_display_name, flow_map
            )
            agent_results = agent_results.append(flow_scan)
        return agent_results

    # Event handlers Main Function
    def find_event_handlers(self):
        """This method extracts event handlers at the flow, page and parameter
        level and displays data about their associated event. A user can use
        this data to spot patterns in event types and look for detrimental
        patterns.

        Args:
          - agent_id must specify agent id when instantiating the class

        Returns:
          - dictionary with flow, page and parameter events
        """
        event_handler_scan = {
            "flow": self._flow_level_handlers(),
            "page": self._page_level_handlers(),
            "parameter": self._parameter_level_handlers(),
        }

        return event_handler_scan

    def get_agent_fulfillment_message_df(self, agent_id, format_message=True):
        fulfillment_df = self.get_raw_agent_fulfillment_df(agent_id)
        fulfillment_message_df = ( 
            fulfillment_df
            .assign(
                response_message = lambda df: df.fulfillment.apply(attrgetter('messages')),
                conditional_cases = lambda df: df.fulfillment.apply(attrgetter('conditional_cases'))
            )
            .drop(columns='fulfillment')
            .explode('response_message', ignore_index=True)
            .assign(
                response_type = lambda df: df.response_message.apply(SearchUtil._get_msg_type)
            )
        )
        if format_message:
            fulfillment_message_df = (fulfillment_message_df.assign(
                    response_message = lambda df: df.response_message.apply(
                        SearchUtil._gather_response_messages)))

        column_order = [
            'flow_name',
            'page_name',
            'parameter_name',
            'event',
            'route_group_name',
            'intent',
            'condition',
            'response_type',
            'response_message',
            'conditional_cases'
        ]

        return fulfillment_message_df[column_order]

    def get_raw_agent_fulfillment_df(self, agent_id):

        flow_df = self.get_flow_df(agent_id)
        page_df = self.get_page_df(flow_df)

        route_group_df = self.get_route_group_df(page_df, list(flow_df.flow_id))
        route_df = self.get_route_df(page_df, route_group_df)
        param_df = self.get_param_df(page_df)
        
        param_initial_prompt_fulfillment_df = param_df[
            ['flow_name', 'page_name', 'parameter_name', 'initial_prompt_fulfillment']
        ]
        param_reprompt_event_handler_df = param_df[
            ['flow_name', 'page_name', 'parameter_name', 'reprompt_event_handlers']
        ]
        event_handler_df = self.get_event_handler_df(page_df, param_reprompt_event_handler_df)

        fulfillment_df = ( 
            pd.concat([
                page_df
                    .drop(columns=
                        ['parameters', 'route_groups', 'routes', 'event_handlers'])
                    .rename(columns={'entry_fulfillment':'fulfillment'}),
                event_handler_df
                    .rename(columns={'trigger_fulfillment':'fulfillment'}),
                route_df
                    .rename(columns={'trigger_fulfillment':'fulfillment'}),
                param_initial_prompt_fulfillment_df
                    .rename(columns={'initial_prompt_fulfillment':'fulfillment'})
            ], ignore_index=True)
            .dropna(subset=['fulfillment'], axis='index')
        )
        return fulfillment_df

    def get_flow_df(self, agent_id):
        flowlist = self.flows.list_flows(agent_id = agent_id)
        flow_df = pd.DataFrame([{
            'flow_name': flow.display_name,
            'flow_id': flow.name,
            'routes': flow.transition_routes,
            'event_handlers':flow.event_handlers,
            'route_groups':flow.transition_route_groups
        } for flow in flowlist])
        return flow_df

    def get_page_df(self, flow_df):
        page_df = (
            flow_df[['flow_name', 'flow_id']]
            .assign(
                page_obj = flow_df.flow_id.apply(self.pages.list_pages))
            .explode('page_obj', ignore_index=True)
            .assign(
                page_name = lambda df: df.page_obj.apply(attrgetter('display_name')),
                entry_fulfillment = lambda df: df.page_obj.apply(attrgetter('entry_fulfillment')),
                parameters = lambda df: df.page_obj.apply(attrgetter('form.parameters')),
                route_groups = lambda df: df.page_obj.apply(attrgetter('transition_route_groups')),
                routes = lambda df: df.page_obj.apply(attrgetter('transition_routes')),
                event_handlers = lambda df: df.page_obj.apply(attrgetter('event_handlers')))
            .drop(columns='page_obj')
        )

        #add in the start pages (flow objects)
        page_df = ( 
            pd.concat([
                page_df, 
                flow_df.assign(page_name = 'START_PAGE')
            ], ignore_index=True)
            .drop(columns='flow_id')
        )
        return page_df

    def get_route_group_df(self, page_df, flow_id_list):
        agent_route_groups = []
        for flow_id in flow_id_list:
            agent_route_groups.extend(self.route_groups.list_transition_route_groups(flow_id))
        rgdict = {rg.name:rg for rg in agent_route_groups}
        
        route_group_df = (
            page_df[['flow_name', 'page_name', 'route_groups']]
            .explode('route_groups', ignore_index=True)
            .dropna(subset=['route_groups'], axis='index')
            .assign(
                #below: map route group ids to route group data structures
                route_groups = lambda df: df.route_groups.map(rgdict),

                route_group_name = lambda df: df.route_groups.apply(attrgetter('display_name')),
                routes = lambda df: df.route_groups.apply(attrgetter('transition_routes')))
            .drop(columns='route_groups')
        )
        return route_group_df

    def get_route_df(self, page_df, route_group_df):
        routes_df = (
            pd.concat([
                page_df[['flow_name', 'page_name', 'routes']],
                route_group_df
            ], ignore_index=True)
            .explode('routes', ignore_index=True)
            .dropna(subset=['routes'], axis='index')
            .assign(
                intent = lambda df: df.routes.apply(attrgetter('intent')),
                condition = lambda df: df.routes.apply(attrgetter('condition')),
                trigger_fulfillment = lambda df: df.routes.apply(attrgetter('trigger_fulfillment'))
            )
            .drop(columns='routes')
        )
        return routes_df

    def get_param_df(self, page_df):
        param_df = (
            page_df[['flow_name', 'page_name', 'parameters']]
            .explode('parameters', ignore_index=True)
            .dropna(subset=['parameters'], axis='index')
            .assign(
                parameter_name = lambda df: df.parameters.apply(attrgetter('display_name')),
                reprompt_event_handlers = lambda df: df.parameters.apply(attrgetter('fill_behavior.reprompt_event_handlers')),
                initial_prompt_fulfillment = lambda df: df.parameters.apply(attrgetter('fill_behavior.initial_prompt_fulfillment'))
            )
            .drop(columns='parameters')
        )
        return param_df

    def get_event_handler_df(self, page_df, param_reprompt_event_handler_df):
        event_handler_df = (
            pd.concat([
                page_df[['flow_name', 'page_name', 'event_handlers']],
                param_reprompt_event_handler_df
                    .rename(columns={'reprompt_event_handlers':'event_handlers'})
            ], ignore_index=True)
            .explode('event_handlers', ignore_index=True)
            .dropna(subset=['event_handlers'], axis='index')
            .assign(
                event = lambda df: df.event_handlers.apply(attrgetter('event')),
                trigger_fulfillment = lambda df: df.event_handlers.apply(attrgetter('trigger_fulfillment'))
            )
            .drop(columns='event_handlers')
        ) 
        return event_handler_df

    @staticmethod
    def _get_msg_type(message):
        if pd.isna(message):
            return np.nan
        elif message.text:
            return 'text'
        elif message.payload:
            return 'custom_payload'
        elif message.play_audio:
            return 'play_audio'
        elif message.live_agent_handoff:
            return 'live_agent_handoff'
        elif message.conversation_success:
            return 'conversation_success'
        elif message.output_audio_text:
            return 'output_audio_text'

    @staticmethod
    def _gather_text_responses(text_message):
        flat_texts = '\n'.join(text_message.text)
        return flat_texts

    @staticmethod
    def _gather_response_messages(msg):
        if pd.isna(msg):
            pass
        elif msg.payload:
            msg = msg.payload
        elif msg.play_audio:
            msg = msg.play_audio
        elif msg.live_agent_handoff:
            msg = msg.live_agent_handoff.metadata
        elif msg.conversation_success:
            msg = msg.conversation_success.metadata
        elif msg.output_audio_text:
            msg = msg.output_audio_text.text
        elif msg.text:
            msg = SearchUtil._gather_text_responses(msg.text)
        return msg

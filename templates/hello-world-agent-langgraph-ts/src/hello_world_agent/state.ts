/**
 * Agent state definition for hello-world-agent.
 */

import { BaseMessage } from "@langchain/core/messages";
import { Annotation } from "@langchain/langgraph";

export const HelloWorldStateAnnotation = Annotation.Root({
  messages: Annotation<BaseMessage[]>({
    reducer: (left, right) => left.concat(right),
    default: () => [],
  }),
});

export type HelloWorldState = typeof HelloWorldStateAnnotation.State;
